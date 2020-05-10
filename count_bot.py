"""
Discoard bot that keeps count of current numbers of face shields in each person's possession until the next drop.

The Count Bot leaches from a Discord server for free storage. This bot does not store anything on the bot host
(AWS or your local desktop). The bot only keeps a in-memory Pandas dataframe of inventory. It keeps this memory
inventory synchronized as along as it lives, with what is stored in Discord. If the bot dies, it can be restarted,
and it will rebuild its memory inventory by reading from Discord.

NOTE: Discord.py isn't available as a Conda package it seems. So it is not specified in meta.yaml. Install directly:
   python -m pip install -U discord.py
   pip install humanize
"""
import discord
import logging
import pandas as pd
import sys
import os
import io
import traceback
import getpass

from pprint import pprint
from functools import lru_cache
from discord.ext import commands
from my_tokens import get_bot_token
from datetime import datetime
from typing import NamedTuple, Optional
from humanize import naturaltime
from collections import OrderedDict

logging.basicConfig(level=logging.INFO)

# CONFIGURATION tailored to a particular Discord server (guild).
INVENTORY_CHANNEL = os.getenv("COUNT_BOT_INVENTORY_CHANNEL", 'bot-inventory')  # The bot only listens to this official text channel, plus personal DM channels
ADMIN_ROLE_NAME = 'botadmin'        # Users who can run 'sudo' commands
COLLECTOR_ROLE_NAME = 'collector'   # Users who collect printed items from makers
PRODUCT_CSV_FILE_NAME = 'product_inventory.csv'  # File name of the product inventory attachment in a sync point
MSG_HISTORY_TROLLING_LIMIT = 4000  # How many messages do we read back from transaction log until we hit a sync point?
CODE_VERSION = '0.5'  # Increment this whenever the schema of persisted inventory csv or trnx logs change

# DEBUG-ONLY configuration - Leave all these debug flags FALSE for production run.
# TODO - Probably should turn into real config parameter stored in _discord_config_no_commit.txt
DEBUG_ = False
DEBUG_DISABLE_STARTUP_INVENTORY_SYNC = DEBUG_  # Disable the inventory sync point recorded at bot start-up
DEBUG_DISABLE_INVENTORY_POSTS_FROM_DM = DEBUG_  # Disable any official inventory posting when testing in DM channel
DEBUG_PRETEND_DM_IS_INVENTORY = DEBUG_  # Make interactions in DM channel mimic behavior seen in official inventory

# FIXME - Leon and Vinny want the bot to generate CSV on demand - probably send to DM channel for now
#         Julie doesn't need forecast counts. She needs actual 'collected' ledger transactions.
# FIXME - add a 'drop-off' command to move items to a dropped box. Collectors add an emoji to confirm. Tnx rebuild looks for reactions in msgs.
#         then add a third role 'dropped', between maker and collector. Implement 'drop' command. Allow collectors to apply msg response.
#         'collect' shows maker's dropped entries if non-empty. 'Report shows 'dropped' in summmary, and as part of 'collectors' detail tables.
#         read new 'drop' transaction log entries with maker and collector names.
# FIXME - add command to produce sync point csv on demand, but only send to DM channel for manual backup
# FIXME - add convenient spying tools: count @Freddie, report @Freddie, and collect @Freddie
# FIXME - add 'collect from <maker>' - same as count @Freddie
# FIXME - add 'collect from <maker> ALL [pru] [pet] - to get all items without specifying the count nor item
# FIXME - add 'delivered' command and a hospital bucket
# FIXME - track historical contributions per person in a separate historical table. Mark an entry for collections and deliveries
# FIXME - prevent two bots from running against the same channel
# FIXME - when reading back trnx log entries - print its msg.created_at value on the left before {:60}
# FIXME - consider making the bot respond if people type in wrong commands that do not exist. Let them know the bot is still alive.
# FIXME - move INVENTORY_CHANNEL and related config params to my_token. They can all come from env vars, or from the locally-cached config file
# FIXME - maybe addd assembly as an item type
# FIXME - look into google sheet API to update it automatically. https://developers.google.com/sheets/api/guides/concepts
# FIXME - 'remove all' generates: PerformanceWarning: dropping on a non-lexsorted multi-index without a level parameter may impact performance.

USER_ROLE_HUMAN_TO_DISCORD_LABEL_MAP = {
    'admins': ADMIN_ROLE_NAME,
    'collectors': COLLECTOR_ROLE_NAME,
}

# Items are things that makers can print or build.
# Use lower-case for item names.
ITEM_CHOICES = {
    'verkstan':  "3D Verkstan head band",
    'prusa':     "Prusa head band",
    'visor':     "Transparency sheet",
    'earsaver':  "Ear saver",
}

VARIANT_CHOICES = {
    'prusa':     ["PETG", "PLA"],
    'verkstan':  ["PETG", "PLA"],
    'visor':     ["prusa", "verkstan"],
    'earsaver':  [" "],
}

ITEMS_WITH_NO_VARIANTS = {
    'earsaver',
}

COL_USER_ID = 'user_id'
COL_USER_NAME = 'user'
COL_ITEM = 'item'
COL_VARIANT = 'variant'
COL_COUNT = 'count'

# DiscordPy returns 'naive' datetime in UTC, not 'aware' datetime.
# For comformity I just use naive UTC datetime as well. These are stored into CSV files also in naive UTC datetime.
COL_UPDATE_TIME = 'update_time'
COL_HUMAN_INTERVAL = 'updated'

COL_SECOND_USER_ID = 'second_user_id'
COL_SECOND_USER_NAME = 'second_user'

COL_MAKER_NAME = 'maker'
COL_COLLECTOR_NAME = 'collector'

USER_ID_COLUMNS = (COL_USER_ID, COL_SECOND_USER_ID)

TIME_DIFF = datetime.utcnow() - datetime.now()

# Personal inventories are where a user alone is part of the primary key.
# This includes maker inventories and collector inventories.
PERSONAL_PRIMARY_KEY = [COL_USER_ID, COL_ITEM, COL_VARIANT]
PERSONAL_DF_COLUMNS = PERSONAL_PRIMARY_KEY + [COL_COUNT, COL_UPDATE_TIME]

# A transaction inventory is where we record a transaction between two users.
# So both users need to be part of the primary key.
TRANSACTION_PRIMARY_KEY = PERSONAL_PRIMARY_KEY + [COL_SECOND_USER_ID]
TRANSACTION_DF_COLUMNS = TRANSACTION_PRIMARY_KEY + [COL_COUNT, COL_UPDATE_TIME]

USER_NAME_LEFT_JUST_WIDTH = 30

USER_ROLE_MAKERS = 'makers'  # Stores what makers have made, but not yet passed onto collectors
USER_ROLE_COLLECTORS = 'collectors'  # Stores what collectors have collected from makers
USER_ROLE_DROPBOXES = 'dropboxes'  # Dropboxes serving as intermediate buffer between makers and collectors

# maps 'makers' (USER_ROLE_MAKERS), 'collectors', etc to dataframes that store per-role inventory
INVENTORY_BY_USER_ROLE = OrderedDict()

# The order of items in this list is important. It is used to persist CSV tables into CSV sync point
USER_ROLES_IN_ORDER = [USER_ROLE_MAKERS, USER_ROLE_COLLECTORS, USER_ROLE_DROPBOXES]

ALIAS_MAPS = {}
ALL_ITEM_VARIANT_COMBOS = []
def _setup_aliases():
    for item, variants in VARIANT_CHOICES.items():
        ALIAS_MAPS[item.lower()] = item  # In case len is less than 3
        ALIAS_MAPS.update([(item[:i].lower(), item) for i in range(3, len(item))])
        for variant in variants:
            ALIAS_MAPS[variant.lower()] = variant  # In case len is less than 3
            ALIAS_MAPS.update([(variant[:i].lower(), variant) for i in range(3, len(variant))])
            ALL_ITEM_VARIANT_COMBOS.append((item, variant))
_setup_aliases()

def _fake_command_prefix_in_right_channel(_bot, message):
    """
    This is really pathetic. All "Checks" (command-specific or global) operate on the raise-an-exception basis.
    If I want use Checks to prevent this bot from responding to all channels except for DM and inventory channels,
    common words such as count, etc. will raise all sorts of exceptions and pollute log for no good reason.
    A workaround is to fake a command prefix in incorrect channels to prevent these channels from being picked up.

    These are meant to be passed into the :attr:`.Bot.command_prefix` attribute.
    """
    ch = message.channel
    if ch.type == discord.ChannelType.private:
        return ''  # no command prefix means that comments from this DM channel will be matched and processed
    elif ch.type != discord.ChannelType.text:
        return '#fake-prefix-no-one-uses#'
    elif ch.name == INVENTORY_CHANNEL:
        return ''
    return '#fake-prefix-no-one-uses#'

description = '''Keep count of current numbers of face shields in each person's possession until the next drop. ''' \
    '''You can talk to this bot in a direct message (DM) channel, or the assigned '{0}' channel. ''' \
    '''Help commands that generate too much output get redirected to your DM channel. ''' \
    '''See 10-min guide at https://github.com/Fred-Hsu/count_bot.''' \
    .format(INVENTORY_CHANNEL)

bot = commands.Bot(
    description=description,
    case_insensitive=True,  # No need to be draconian with case
    command_prefix=_fake_command_prefix_in_right_channel,
    help_command=commands.DefaultHelpCommand(
        no_category='Commands',
        dm_help=True,   # Set to True to redirect help text that are too long to user's own DM channels
    ),
    # max_messages=10000,  # I found this not to be useful. Doesn't help getting processed reactions.
)

class NotEntitledError(commands.errors.CommandError):
    pass

class NegativeCount(commands.errors.CommandError):
    pass

def my_naturaltime(dt):
    return naturaltime(dt - TIME_DIFF)

@bot.listen()
async def on_command_error(ctx, error):
    """
    DiscordPy command failures are horrible. If input argument as much as fail a tpye checking/conversion,
    callstack is printed, and nothing is send back to the user as feedback. Explicitly catch certain types of
    user error here, and provide 'help' feedback instead of silently failing.

    This is a global error handler for all commands. Each command can also provide its own specific error handler.
    """
    if isinstance(error, (NotEntitledError, NegativeCount)):
        pass
    elif isinstance(error, (commands.errors.BadArgument, commands.errors.MissingRequiredArgument)):
        await ctx.send("❌  I don't completely understand. See help.")
        await ctx.send_help(ctx.command)
    else:
        # If this listener doesn't exist, the Bot.on_command_error does this:
        print('Ignoring exception in command {}:'.format(ctx.command), file=sys.stderr)
        traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)

async def _post_sync_point_to_trans_log():
    s_buf = io.StringIO()

    for _role, inventory_df in INVENTORY_BY_USER_ROLE.items():
        modified_df = await _add_user_display_name_column(inventory_df)
        modified_df.to_csv(s_buf, index=False)
        s_buf.write('\n')

    s_buf.write("version\n'{0}'\n".format(CODE_VERSION))
    s_buf.seek(0)
    file = discord.File(s_buf, PRODUCT_CSV_FILE_NAME)
    sync_text = '✅ ' + "Bot restarted: sync point"

    if DEBUG_DISABLE_STARTUP_INVENTORY_SYNC:
        # FIXME - remove hardcoded user...
        guild = _get_first_guild()
        member = guild.get_member(700184823628562482)
        await member.send('DEBUG: record in DM: ' + sync_text, file=file)
        print('Posted a CSV sync point message on DM')
    else:
        ch = _get_inventory_channel()
        await ch.send(sync_text, file=file)
        print('Posted a CSV sync point message on inventory channel')

@bot.event
async def on_ready():
    print('Logged in as')
    print(bot.user.name)
    print(bot.user.id)
    print('---- rebuilding inventory from log')
    updates_since_sync_point = await _retrieve_inventory_df_from_transaction_log()
    if updates_since_sync_point:
        print('---- writing inventory sync point to log')
        await _post_sync_point_to_trans_log()
    print('---- ready')

# on_reaction_add - this only works if the bot was monitoring messages that reactions operated on.
# If the reaction tags a message that was posted before this bot was rebooted, then the past
# message will not be in the "internal message cache", and thus on_reaction_add won't be triggered.
# Increasing Bot.max_messages doesn't help.
# @bot.event
# async def on_reaction_add(reaction, user):
#     print("Reaction: {0} {1}".format(user, reaction))

# TODO - temporarily commented out. Will be resurrected when finish implementing reaction-based collection.
# @bot.event
# async def on_raw_reaction_add(payload):
#     print("Reaction add: {0}".format(payload))
#
#     if payload.guild_id is None:
#         print('  Ignore DM reactions')
#         return
#
#     if payload.emoji.name != '💯':
#         print('  Ignore emojis that are not 💯')
#         return
#
#     is_collector = await _user_has_role(payload.member, COLLECTOR_ROLE_NAME)
#     if not is_collector:
#         print('  Ignore reactions from non-collectors')
#         return
#
#     collector = payload.member
#     ch = _get_inventory_channel()
#     msg = await ch.fetch_message(payload.message_id)
#
#     result = parse_dropbox_count_trnx_log_entry(msg)
#     if not result:
#         print('  Not a dropbox count record')
#         return
#
#     maker, collector, count, item, variant = result
#     # FIXME - not yet done
#
# @bot.event
# async def on_raw_reaction_remove(payload):
#     print("Reaction remove: {0}".format(payload))

# Never got this triggered. Not sure how this action happens.
# @bot.event
# async def on_raw_reaction_clear(payload):
#     print("Reaction clear: {0}".format(payload))

@lru_cache()
def _get_inventory_channel():
    """Find the right inventory channel model"""
    for ch in bot.get_all_channels():
        if ch.name == INVENTORY_CHANNEL:
            return ch
    raise RuntimeError('No channel named "{0}" found'.format(INVENTORY_CHANNEL))

class RoleBootstrap:
    # This tracks the last action performed by a user on an item + variant.
    # Only the last action of said tuple is used to rebuild the inventory.
    # Any previous action by the user on said tuple are ignored.
    last_action = {}
    role_name = ''
    sync_point_df = pd.DataFrame()
    inventory_df = pd.DataFrame()

    df_columns = PERSONAL_DF_COLUMNS
    primary_key = PERSONAL_PRIMARY_KEY

    def __init__(self, role_name):
        self.role_name = role_name
        self.last_action = {}
        self.sync_point_df = pd.DataFrame(columns=self.df_columns)
        self.inventory_df = None

    def read_sync_point_csv(self, csv_text):
        sync_df = pd.read_csv(io.StringIO(csv_text))
        if COL_UPDATE_TIME in sync_df:  # From V0.1 to V0.3 - CSV did not have update-time before V0.3
            sync_df = pd.read_csv(io.StringIO(csv_text), parse_dates=[COL_UPDATE_TIME])

        if COL_USER_NAME in sync_df:  # Code before V0.3 did not have user name column
            sync_df.drop(columns=COL_USER_NAME, inplace=True)

        if COL_UPDATE_TIME not in sync_df:  # CSV before V0.3 did not have update time column
            sync_df[COL_UPDATE_TIME] = datetime.utcnow()

        self.sync_point_df = sync_df

    def rebuild_inventory_df_from_sync_n_updates(self):
        rows = []
        for (user_id, item, variant), action in self.last_action.items():
            if action.count is not None:
                rows.append((user_id, item, variant, action.count, action.update_time))

        for index, row in self.sync_point_df.iterrows():
            key = (row[COL_USER_ID], row[COL_ITEM], row[COL_VARIANT])
            if key not in self.last_action:
                rows.append(
                    (row[COL_USER_ID], row[COL_ITEM], row[COL_VARIANT], row[COL_COUNT], row[COL_UPDATE_TIME]))
        pprint(rows, width=120)

        df = pd.DataFrame(rows, columns=self.df_columns)
        df.set_index(keys=self.primary_key, inplace=True, verify_integrity=True, drop=False)
        self.inventory_df = df

class TransactionRoleBootstrap(RoleBootstrap):
    df_columns = TRANSACTION_DF_COLUMNS
    primary_key = TRANSACTION_PRIMARY_KEY

    def rebuild_inventory_df_from_sync_n_updates(self):
        rows = []
        for (maker_id, collector_id, item, variant), action in self.last_action.items():
            # Do not record '0' count in transaction tables such as drop-box
            if action.count:
                rows.append((maker_id, item, variant, collector_id, action.count, action.update_time))

        for index, row in self.sync_point_df.iterrows():
            key = (row[COL_USER_ID], row[COL_SECOND_USER_ID], row[COL_ITEM], row[COL_VARIANT])
            if key not in self.last_action and row[COL_COUNT] != 0:
                rows.append((row[COL_USER_ID], row[COL_ITEM], row[COL_VARIANT], row[COL_SECOND_USER_ID],
                             row[COL_COUNT], row[COL_UPDATE_TIME]))
        pprint(rows, width=120)

        df = pd.DataFrame(rows, columns=self.df_columns)
        df.set_index(keys=self.primary_key, inplace=True, verify_integrity=True, drop=False)
        self.inventory_df = df

BOOTSTRAP_CLASS_BY_USER_ROLE = {
    USER_ROLE_MAKERS:       RoleBootstrap,
    USER_ROLE_COLLECTORS:   RoleBootstrap,
    USER_ROLE_DROPBOXES:    TransactionRoleBootstrap,
}

# TODO - temporarily commented out. Will be resurrected when finish implementing reaction-based collection.
# def parse_dropbox_count_trnx_log_entry(msg):
#     text = msg.content
#     if msg.author != bot.user:
#         return
#     if not text.startswith('✅ '):
#         return
#     if not msg.mentions:
#         return
#
#     if text.endswith(' (from DM chat)'):
#         text = text[:-15]
#
#     result = text.rsplit(maxsplit=7)
#     if len(result) != 8:
#         return
#
#     _check, maker_plus_semi, _drop, _count, collector_str, count, item, variant = result
#     if _drop != 'dropbox' or _count != 'count':
#         return
#
#     maker_str, _semi = maker_plus_semi.split(':')
#     maker_str = maker_str.strip('<@!>')
#     collector_str = collector_str.strip('<@!>')
#     count = int(count)
#
#     mention_map = dict([(str(m.id), m) for m in msg.mentions])
#     maker = mention_map[maker_str]
#     collector = mention_map[collector_str]
#
#     return maker, collector, count, item, variant

class TransLogAction(NamedTuple):
    count: Optional[int]
    update_time: datetime

async def _process_one_trans_record(member, last_action, text, item, variant, command, update_time, collector):
    if not collector:
        key = (member.id, item, variant)
    else:
        key = (member.id, collector.id, item, variant)

    if key in last_action:
        print("{} {:60} {}".format(update_time, text, 'superseded by count or remove'))
        return
    else:
        if (item, variant) == ('remove', 'all'):
            for combo in ALL_ITEM_VARIANT_COMBOS:
                combo_key = (member.id, combo[0], combo[1])
                if combo_key not in last_action:
                    last_action[combo_key] = TransLogAction(None, update_time)
            print("{} {:80} {}".format(update_time, text, 'remove all'))
            return
        elif command.startswith('remove'):
            last_action[key] = TransLogAction(None, update_time)
            print("{} {:80} {}".format(update_time, text, command))
        elif command.startswith('count'):
            parts = command.split()
            last_action[key] = TransLogAction(int(parts[1]), update_time)
            print("{} {:80} {}".format(update_time, text, command))
        else:
            print("{} {:80} {}".format(update_time, text, 'I DO NOT UNDERSTAND THIS COMMAND'))

async def _retrieve_inventory_df_from_transaction_log() -> int:
    """
    Troll through inventory channel's message records to find all relevant transactions until we hit a sync point.
    Use these to rebuild in memory the inventory dataframe.
    """
    ch = _get_inventory_channel()

    bootstrap_by_role = OrderedDict()
    for role_name in USER_ROLES_IN_ORDER:
        # Make sure to add them in the right order so we can do simply do iteration when order is important.
        cls = BOOTSTRAP_CLASS_BY_USER_ROLE[role_name]
        bootstrap_by_role[role_name] = cls(role_name)

    # Channel history is returned in reverse chronological order.
    # Troll through these entries and process only transaction log-type messages posted by the bot itself.
    async for msg in ch.history(limit=MSG_HISTORY_TROLLING_LIMIT):
        text = msg.content

        if msg.author != bot.user:
            continue
        if not text.startswith('✅ '):
            continue

        if text.endswith(' (from DM chat)'):
            text = text[:-15]

        if text.endswith('sync point'):
            if not msg.attachments:
                print('Internal error - found a syncpoint without attachment. Continue trolling...')
                continue
            product_att = msg.attachments[0]
            print('Found attachment: ' + product_att.filename)
            if product_att.filename != PRODUCT_CSV_FILE_NAME:
                print('Internal error - wrong inventory file found. Continue trolling...')
                continue

            csv_mem = io.BytesIO(await product_att.read())
            csv_text = csv_mem.getvalue()
            csv_text = str(csv_text, 'utf-8')

            tables = csv_text.split('\n\n')
            tables, version = tables[:-1], tables[-1]

            for i, role_name in enumerate(USER_ROLES_IN_ORDER):
                if i >= len(tables):
                    # Before V0.4, there were only makers and collectors tables.
                    break
                print("  parsing csv table for: ", role_name)
                csv_text = tables[i]
                bootstrap_by_role[role_name].read_sync_point_csv(csv_text)

            print("{} {:80} sync point - stop trolling".format(msg.created_at, text))
            break

        if msg.mentions:
            # Messages with mentions are records created in response to a user action.
            # The order of the mentions list is not in any particular order so you should not rely on it.
            # This is a discord limitation, not one with the library.
            mention_map = dict([(str(m.id), m) for m in msg.mentions])

            collector = None
            head, item, variant = text.rsplit(maxsplit=2)
            if variant in ITEMS_WITH_NO_VARIANTS:
                head += ' ' + item
                item = variant
                variant = " "

            member_prefix, command_head = head.split(':')
            _garbage, member_str = member_prefix.rsplit(maxsplit=1)
            member_str = member_str.strip('<@!>')
            member = mention_map[member_str]

            command_head = command_head.strip()
            if command_head.startswith('collect'):
                last_action = bootstrap_by_role[USER_ROLE_COLLECTORS].last_action
                if (item, variant) != ('remove', 'all'):
                    _garbage, command = command_head.split(maxsplit=1)
                else:
                    command = ''
            elif command_head.startswith('drop'):
                last_action = bootstrap_by_role[USER_ROLE_DROPBOXES].last_action
                _cmd, collector_str, count = command_head.split(maxsplit=3)
                collector_str = collector_str.strip('<@!>')
                collector = mention_map[collector_str]
                command = 'count ' + count
            else:
                last_action = bootstrap_by_role[USER_ROLE_MAKERS].last_action
                command = command_head
            await _process_one_trans_record(member, last_action, text, item, variant, command, msg.created_at, collector)

    print('  --- updates since last syncpoint --')

    updates_since_sync_point = 0
    for role_name, bootstrap in bootstrap_by_role.items():
        print(role_name)
        pprint(bootstrap.last_action)
        updates_since_sync_point += len(bootstrap.last_action)
    print('updates since last sync point: ', updates_since_sync_point)

    print('  --- rebuilt inventory --')

    for role_name, bootstrap in bootstrap_by_role.items():
        print('sync point: ', role_name)
        print(bootstrap.sync_point_df.to_string(index=False))
        print('building inventory df from sync point and updates: ', role_name)
        bootstrap.rebuild_inventory_df_from_sync_n_updates()

    for role_name in USER_ROLES_IN_ORDER:
        # Make sure to add them in the right order so we can do simply do iteration when order is important.
        INVENTORY_BY_USER_ROLE[role_name] = bootstrap_by_role[role_name].inventory_df

    return updates_since_sync_point

def _add_human_interval_col(df):
    new_col = df.apply(lambda row: my_naturaltime(row[COL_UPDATE_TIME]), axis=1)
    return df.assign(**{COL_HUMAN_INTERVAL: new_col.values})

async def _send_df_as_msg_to_user(ctx, df, prefix=''):
    if not len(df):
        await ctx.send(prefix + "```(no inventory records)```")
    else:
        result = _add_human_interval_col(df)
        result = result.loc[:, [COL_COUNT, COL_ITEM, COL_VARIANT, COL_HUMAN_INTERVAL]]
        result = result.sort_index(axis='index')
        await ctx.send(prefix + "```{0}```".format(result.to_string(index=False)))

async def _send_dropbox_df_as_msg_to_user(ctx, df, prefix=''):
    if not len(df):
        await ctx.send(prefix + "```(no dropbox records)```")
    else:
        dropped = df.drop(columns=COL_USER_ID)
        mapped = await _map_user_id_column_to_display_names(dropped)
        renamed = mapped.rename(columns={COL_SECOND_USER_ID: COL_COLLECTOR_NAME})
        result = _add_human_interval_col(renamed)
        result = result.loc[:, [COL_COUNT, COL_ITEM, COL_VARIANT, COL_COLLECTOR_NAME, COL_HUMAN_INTERVAL]]
        result = result.sort_index(axis='index')
        await ctx.send(prefix + "```{0}```".format(result.to_string(index=False)))

async def _resolve_item_name(ctx, item):
    item_name = ALIAS_MAPS.get(item.lower())
    if not item_name:
        await ctx.send("❌  Item '{0}' is not something I know about. See help.".format(item))
        await ctx.send_help(ctx.command)
        return None
    if item_name not in ITEM_CHOICES:
        await ctx.send("❌  '{0}' is not valid item. See help.".format(item_name))
        await ctx.send_help(ctx.command)
        return None
    return item_name

async def _resolve_variant_name(ctx, item, variant):
    variant_name = ALIAS_MAPS.get(variant.lower())
    if not variant_name:
        await ctx.send("❌  Variant '{0}' is not something I know about. See help.".format(variant))
        await ctx.send_help(ctx.command)
        return None
    variants = VARIANT_CHOICES.get(item)
    if variant_name not in variants:
        await ctx.send("❌  '{0}' is not valid variant of item '{1}'. See help.".format(variant_name, item))
        await ctx.send_help(ctx.command)
        return None
    return variant_name

async def _post_user_record_to_trans_log(ctx, command_text, detail_text):
    """
    All valid transactions must begin with '✅ '.
    Do not post transaction messages without calling this function.
    """

    # Only members of associated guilds can post transactions.
    # This is the last line of defense against random users DM'ins the bot to cause DoS attacks.
    # The function will raise exception of user is not in the guild.
    await _map_dm_user_to_member(ctx.message.author)

    trans_text = '{0}: {1} {2}'.format(ctx.message.author.mention, command_text, detail_text)

    if ctx.message.channel.type == discord.ChannelType.private:
        await ctx.send("Command processed. Transaction posted to channel '{0}'.".format(INVENTORY_CHANNEL))
        # If private DM channel, also post to inventory channel
        ch = _get_inventory_channel()
        if DEBUG_DISABLE_INVENTORY_POSTS_FROM_DM:
            await ctx.send('DEBUG: record in DM: ✅ ' + trans_text)
        else:
            await ch.send('✅ ' + trans_text + ' (from DM chat)')
    else:
        await ctx.send('✅ ' + trans_text)

async def show_maker_inventory_and_dropbox(ctx):
    maker_id = ctx.message.author.id
    maker_df = INVENTORY_BY_USER_ROLE[USER_ROLE_MAKERS]
    maker_cond = maker_df[COL_USER_ID] == maker_id

    await _send_df_as_msg_to_user(ctx, maker_df[maker_cond], prefix="Your maker inventory:")

    dropbox_df = INVENTORY_BY_USER_ROLE[USER_ROLE_DROPBOXES]
    dropbox_cond = dropbox_df[COL_USER_ID] == maker_id
    dropbox_found_num = sum(dropbox_cond)

    if dropbox_found_num:
        await _send_dropbox_df_as_msg_to_user(ctx, dropbox_df[dropbox_cond], prefix="Items you dropped off:")

@bot.command(
    brief="Update the current count of items from a maker",
    description='Update the current {total} count of an {item} of {variant} type from a maker:')
async def count(ctx, total: int = None, item: str = None, variant: str = None):
    """
Item and variant choices are shown below. Words are case-insensitive. \
You can also use aliases such as 'ver', 'verk', 'pru', 'pet' \
and 'vis', 'viso', etc. to refer to the the full item and variant names. Item and variant choices:

verkstan    PETG or PLA
prusa       PETG or PLA
visor       verkstan or prusa
earsaver

count - shortcut to print out items under your possession.'.
count 20 - shortcut to update a single item you make, to a total count of 20.
count 20 prusa - shortcut to update a single variant of prusa shield you make.
"""

    # The help doc is too long.... Once I figure out how to show collapsible text, resurrect these:
    #
    # item        variant
    # --------------------
    # verkstan    PETG or PLA
    # prusa       PETG or PLA
    # visor       verkstan or prusa
    #
    # count
    # If you have already recorded items in the system before, you can use "count" without any arguments \
    # to show items you have recorded.
    #
    # count [total]
    # If you only have one item in the system, you can keep updating its current count without re-specifying \
    # the item type and the variant type.
    #
    # count [total] [item]
    # If you only print one variant of an item in the system, you can update its current count without re-specifying \
    # the variant type.

    print('Command: count {0} {1} {2} ({3})'.format(total, item, variant, ctx.message.author.display_name))
    await _count(ctx, total, item, variant)

async def _count(ctx, total: int = None, item: str = None, variant: str = None, delta: bool = False,
                 role=USER_ROLE_MAKERS, trial_run_only=False):
    """
    Internal implementation of count, add and reset.
    This is one of the very few fundamental methods that produces a command record in the transaction log.
    Many user commands get translated into this basic command record to perform actual changes to the inventory.
    """

    if isinstance(total, str):
        # This is needed for 'sudo' command to invoke this function without the benefit of built-in convertors.
        total = int(total)

    if role == USER_ROLE_MAKERS and not item and not variant and total is None:
        await show_maker_inventory_and_dropbox(ctx)
        return

    df = INVENTORY_BY_USER_ROLE[role]

    user_id = ctx.message.author.id
    cond = df[COL_USER_ID] == user_id
    found_num = sum(cond)

    if total is None:
        # "count" without argument with existing inventory.

        if not found_num:
            await ctx.send('You have not recorded any item types yet.')
            await ctx.send_help(ctx.command)
            return

        result = df[cond]
        await _send_df_as_msg_to_user(ctx, result)
        return

    elif not item or not variant:
        # Note that if item is None, then variant must also be None

        # The user is updating count without fully specifying both item and variant args.
        # Either variant is not provided, or both item and variant are not provided.
        # Do a search to see if it is possible to narrow down recorded items to just one item.

        if not item:
            # Aka, Only count is specified.

            if not found_num:
                await ctx.send('❌  You have not recorded any item types yet. Cannot update count without a specific item.')
                await ctx.send_help(ctx.command)
                return

            elif found_num > 1:
                await ctx.send("❌  Found more than one type of item. Please be more specific with item type. "
                    "Or use 'reset' to remove item types. See help.")
                await _send_df_as_msg_to_user(ctx, df[cond])
                await ctx.send_help(ctx.command)
                return

            # Fall through on purpose, to update count
            pass

        else:
            # Aka, Count and item are specified, Variant is not specified.
            item = await _resolve_item_name(ctx, item)
            if not item:
                return

            # Change condition to add the item as a filter
            cond = (df[COL_USER_ID] == user_id) & (df[COL_ITEM] == item)
            found_num = sum(cond)

            # Some items have no variants
            supported_variants = VARIANT_CHOICES.get(item)
            if supported_variants == [" "]:
                variant = " "

            elif found_num == 0:
                await ctx.send("❌  You have no recorded variant of type '{0}'. Please specify a variant.".format(item))
                await _send_df_as_msg_to_user(ctx, df[cond])
                await ctx.send_help(ctx.command)
                return

            elif found_num > 1:
                await ctx.send("❌  Found more than one variant of item. Please be more specific with variant name. "
                    "Or use 'reset' to remove item types. See help.")
                await _send_df_as_msg_to_user(ctx, df[cond])
                await ctx.send_help(ctx.command)
                return

        if found_num == 1:
            # There is only one row in the record. Retrieve item and variant names from the single record.
            row = df[cond]
            item = row[COL_ITEM][0]
            variant = row[COL_VARIANT][0]

    item = await _resolve_item_name(ctx, item)
    if not item:
        return

    variant = await _resolve_variant_name(ctx, item, variant)
    if not variant:
        return

    current_count = 0
    cond = (df[COL_USER_ID] == user_id) & (df[COL_ITEM] == item) & (df[COL_VARIANT] == variant)
    rows = df[cond]
    if len(rows) == 1:
        row = rows.iloc[0]
        current_count = row[COL_COUNT]

    if delta:
        # this is not an update of current count, but a delta addition to current count.
            total += current_count

    if total < 0:
        await ctx.send("❌  This results in a negative count of '{0}'. Current '{1}' count is {2}.".format(
            total, role, current_count))
        raise NegativeCount()

    if trial_run_only:
        return total, item, variant

    txt = '{0} {1} {2}'.format(total, item, variant)
    await _post_user_record_to_trans_log(ctx, 'count' if role == USER_ROLE_MAKERS else 'collect count', txt)

    # Only update memory DF after we have persisted the message to the inventory channel.
    # Think of the inventory channel as "disk", the permanent store.
    # If the bot crashes right here, it can always restore its previous state by trolling through the inventory
    # channel and all DM rooms, to find user commands it has not successfully processed.
    df.loc[(user_id, item, variant)] = [user_id, item, variant, total, datetime.utcnow()]
    msg_prefix = "previous count: {0}  delta: {1}".format(current_count, total-current_count)
    await _send_df_as_msg_to_user(ctx, df[(df[COL_USER_ID] == user_id)], prefix=msg_prefix)

@bot.command(
    brief="Same as 'count 0'")
async def reset(ctx, item: str = None, variant: str = None):
    """
Reset the count of an item to 0. This is basically an alias for 'count 0'. \
'Reset' is not the same as 'remove'. Use 'reset' after a drop to reset your count, \
so you can print more and update the count later. 'Remove' on the other hand will \
remove an item, indicating that you do not plan to print more of that item type.

reset -> count 0
reset prusa -> count 0 prusa
reset prusa PETG -> count 0 prusa PETG
"""
    print('Command: reset {0} {1} ({2})'.format(item, variant, ctx.message.author.display_name))
    await _count(ctx, 0, item, variant)

@bot.command(
    brief="Similar to 'count', but it adds instead of updating count",
    description="Add n items to the current count of item/variant from a maker:")
async def add(ctx, num: int = None, item: str = None, variant: str = None):
    """
See 'help count' for descriptions of [item] and [variant], and how you can use shorter aliases to reference them.

add - shortcut to show items you make. Same as 'count' without arguments.
add 20 - shortcut to add 20 to the running count of a single item you make.
add 20 prusa - add 20 to current count of a single variant of prusa shield.
"""
    print('Command: add {0} {1} {2} ({3})'.format(num, item, variant, ctx.message.author.display_name))
    await _count(ctx, num, item, variant, delta=True)

@bot.command(
    brief="Remove an item type you no longer make",
    description="Remove an item type you no longer make:")
async def remove(ctx, item: str = None, variant: str = None):
    """
Items and variants are case-insensitive. You can also use aliases such as 'ver', 'verk', 'pru', 'pet' \
and 'vis', 'viso', etc. to refer to the the full item and variant names. To see your inventory records, \
type 'count'. 'Remove' is not the same as 'reset'. Use 'remove' when you no longer print a certain item.

remove - shortcut to remove the only item you have in the record.
remove [item] - shortcut to update a single variant of an item type.
remove all - special command to wipe out all records of this user.
"""
    print('Command: remove {0} {1} ({2})'.format(item, variant, ctx.message.author.display_name))
    await _remove(ctx, item, variant)

async def _remove(ctx, item: str = None, variant: str = None, role=USER_ROLE_MAKERS):
    """
    Internal implementation of 'remove'.
    This is one of the very few fundamental methods that produces a command record in the transaction log.
    Many user commands get translated into this basic command record to perform actual changes to the inventory.
    """

    df = INVENTORY_BY_USER_ROLE[role]

    user_id = ctx.message.author.id
    cond = df[COL_USER_ID] == user_id
    found_num = sum(cond)

    if not found_num:
        await ctx.send('❌  You have not recorded any item types. There is nothing to remove.')
        return

    if item == 'all':
        await _post_user_record_to_trans_log(ctx, 'remove' if role == USER_ROLE_MAKERS else 'collect remove', 'all')

        # Only update memory DF after we have persisted the message to the inventory channel.
        df.drop(user_id, inplace=True)
        await ctx.send('All your records have been removed')
        return

    if not item and not variant:
        if found_num == 1:
            # There is only one row in the record. Retrieve item and variant names from the single record.
            row = df[cond]
            item = row[COL_ITEM][0]
            variant = row[COL_VARIANT][0]
            # Fall through to normal code which updates the count
        else:
            await ctx.send("❌  Found more than one types of items. Please be more specific. See help.")
            await _send_df_as_msg_to_user(ctx, df[cond])
            await ctx.send_help(ctx.command)
            return

    if item and not variant:
        # Variant is not specified.
        item = await _resolve_item_name(ctx, item)
        if not item:
            return

        cond = (df[COL_USER_ID] == user_id) & (df[COL_ITEM] == item)
        found_num = sum(cond)

        if found_num == 1:
            # There is only one row in the record. Retrieve item and variant names from the single record.
            row = df[cond]
            variant = row[COL_VARIANT][0]
            # Fall through to normal code which updates the count
        elif found_num > 1:
            await ctx.send("❌  Found more than one variant of item '{0}'. "
                "Please be more specific. See help.".format(item))
            await _send_df_as_msg_to_user(ctx, df[cond])
            await ctx.send_help(ctx.command)
            return
        else:
            await ctx.send("❌  You have no more items of this type to remove.")
            return

    item = await _resolve_item_name(ctx, item)
    if not item:
        return

    variant = await _resolve_variant_name(ctx, item, variant)
    if not variant:
        return

    cond = (df[COL_USER_ID] == user_id) & (df[COL_ITEM] == item) & (df[COL_VARIANT] == variant)
    rows = df[cond]
    if len(rows) == 0:
        await ctx.send("❌  You have no more items of this type to remove.")
        return
    elif len(rows) != 1:
        txt = '❌  Internal error - got more than one row - this is not expected. Abort.'
        print(txt)
        await ctx.send(txt)
        return

    txt = '{0} {1}'.format(item, variant)
    await _post_user_record_to_trans_log(ctx, 'remove' if role == USER_ROLE_MAKERS else 'collect remove', txt)

    # Only update memory DF after we have persisted the message to the inventory channel.
    df.drop((user_id, item, variant), inplace=True)
    await _send_df_as_msg_to_user(ctx, df[(df[COL_USER_ID] == user_id)])

def _get_first_guild():
    # This bot can't be run in more than one guild (server), otherwise it gets really screwed up.
    if len(bot.guilds) > 1:
        raise RuntimeError('This bot can server only one server/guild. Found "{0}"'.format(len(bot.guilds)))
    return bot.guilds[0]

async def _map_dm_user_to_member(user):
    # If a 'user' comes from a DM channel, it has a "User" class, not associated to any guild nor roles.
    # Otherwise, user is of "Member" class, with a list of associated roles.
    if isinstance(user, discord.Member):
        return user

    if isinstance(user, discord.User):
        guild = _get_first_guild()
        member = guild.get_member(user.id)
        if member:
            return member
        raise RuntimeError('User "{0}" not a member of my associated guild'.format(user))

    raise RuntimeError('Unexpected type for "{0}"'.format(user))

async def _map_user_ids_to_display_names(ids, pad_for_print=True):
    # If a user id isn't found to be associated to this guild, it will not be included in the returned map.
    guild = _get_first_guild()
    mapped = {}
    for the_id in ids:
        member = guild.get_member(the_id)
        if member:
            mapped[the_id] = member.display_name.ljust(USER_NAME_LEFT_JUST_WIDTH) \
                if pad_for_print else member.display_name
    return mapped

async def _map_user_id_column_to_display_names(df):
    ids = set()
    for user_col_name in USER_ID_COLUMNS:
        if user_col_name in df:
            ids = ids.union(df[user_col_name].unique().tolist())
    mapped = await _map_user_ids_to_display_names(ids)
    return df.replace(mapped)

async def _add_user_display_name_column(df):
    if len(df) == 0:
        return df.assign(**{COL_USER_NAME: ''})

    ids = df.user_id.unique()
    mapped = await _map_user_ids_to_display_names(ids, pad_for_print=False)
    new_col = df.apply(lambda row: mapped[row[COL_USER_ID]], axis=1)
    return df.assign(**{COL_USER_NAME: new_col.values})

@bot.command(
    brief="Report total inventory in the system",
    description="Report inventory of items by all users, broken down by item, variant and user.")
async def report(ctx, item: str = None, variant: str = None):
    """
'item' and 'variant' are optional. Use them to limit the types of items to report.

We encourage people to ask for reports by talking directly to Count Bot from their own DM channel. \
This way the long report does not spam everyone in the inventory channel. \
Every time a report command is used, a brief summary is posted in the inventory, \
and the actual report is sent to the user's own DM channel, regardless of whether the report was requested \
from the inventory channel or DM channel.
"""
    print('Command: report {0} {1} ({2})'.format(item, variant, ctx.message.author.display_name))

    num_records = [len(inventory_df) for inventory_df in INVENTORY_BY_USER_ROLE.values()]
    if not num_records:
        await ctx.send('There are no records in the system yet.')
        return

    async def filter_df(df, item, variant):
        if item:
            item = await _resolve_item_name(ctx, item)
            if not item:
                return pd.DataFrame().reindex_like(df)
            df = df[df[COL_ITEM] == item]

        if variant:
            # If variant exists, then item is also specified
            variant = await _resolve_variant_name(ctx, item, variant)
            if not variant:
                return pd.DataFrame().reindex_like(df)
            df = df[df[COL_VARIANT] == variant]
        return df

    filtered = OrderedDict([(role_name, await filter_df(inventory_df, item, variant))
        for role_name, inventory_df in INVENTORY_BY_USER_ROLE.items()])
    num_records = [len(df) for df in filtered.values()]
    if not num_records:
        await ctx.send('No records found for specified item/variant')
        return

    async def regroup_df(df):
        mapped = await _map_user_id_column_to_display_names(df)
        renamed = mapped.rename(columns={COL_USER_ID: COL_USER_NAME})
        if COL_SECOND_USER_ID in renamed:
            renamed = renamed.rename(columns={COL_SECOND_USER_ID: COL_COLLECTOR_NAME})
        repivoted = renamed.set_index(keys=[COL_ITEM, COL_VARIANT], drop=True)
        groups = repivoted.groupby([COL_ITEM, COL_VARIANT], sort=True)
        return groups

    maker_groups = await regroup_df(filtered[USER_ROLE_MAKERS])
    dropbox_groups = await regroup_df(filtered[USER_ROLE_DROPBOXES])
    collector_groups = await regroup_df(filtered[USER_ROLE_COLLECTORS])

    def get_group(g, key):
        if key in g.groups:
            return g.get_group(key)
        return None

    # Compute total summaries for item/variant

    total_table = pd.DataFrame(columns=[COL_ITEM, COL_VARIANT, "TOTAL", "maker", "dropbox", "collector"])

    for (com_item, com_variant) in ALL_ITEM_VARIANT_COMBOS:
        maker_table = get_group(maker_groups, (com_item, com_variant))
        dropbox_table = get_group(dropbox_groups, (com_item, com_variant))
        collector_table = get_group(collector_groups, (com_item, com_variant))

        maker_total = maker_table[COL_COUNT].sum() if maker_table is not None else 0
        dropbox_total = dropbox_table[COL_COUNT].sum() if dropbox_table is not None else 0
        collector_total = collector_table[COL_COUNT].sum() if collector_table is not None else 0
        grand_total = maker_total + dropbox_total + collector_total
        if not grand_total:
            continue
        total_table.loc[len(total_table)] = [com_item, com_variant, grand_total, maker_total, dropbox_total, collector_total]

    # Compute detailed tables per item/variant

    def process_groups(groups, sort_columns=None):
        processed_list = []
        for index, table in groups:
            # Note that 'sparsify' works on all index columns, except for the very last index column.
            sort_columns = sort_columns or [COL_USER_NAME]
            ordered = table.sort_values(sort_columns)
            ordered = ordered[[COL_COUNT] + sort_columns + [COL_UPDATE_TIME]]
            ordered = _add_human_interval_col(ordered)
            ordered = ordered.drop(columns=COL_UPDATE_TIME)

            total = ordered[COL_COUNT].sum()
            total_line = "{0} {1} = {2} TOTAL".format(index[0], index[1], total)
            processed_list.append("```{0}\n{1}```".format(total_line, ordered.to_string(index=False, header=False)))
        return processed_list

    maker_processed = process_groups(maker_groups)
    dropbox_processed = process_groups(dropbox_groups, sort_columns=[COL_USER_NAME, COL_COLLECTOR_NAME])
    collector_processed = process_groups(collector_groups)

    detailed_breakdowns = []
    for processed_label, processed in (('Makers', maker_processed),
                                       ('Dropboxes', dropbox_processed),
                                       ('Collectors', collector_processed)):
        if processed:
            breakdown= processed_label
            breakdown += ''.join(processed)

            # FIXME - check that breakdown is less than 2,000 chars. Trim it and add disclaimer about chopped-off content.
            detailed_breakdowns.append(breakdown)

    if ctx.message.channel.type == discord.ChannelType.private and not DEBUG_PRETEND_DM_IS_INVENTORY:
        msg = "Summary:\n```{0}```".format(total_table.to_string(index=False))
        await ctx.send(msg)

        # I have to break up different roles. Each Discord message has a server-side hardl imit of 2,000.
        for detail_by_role in detailed_breakdowns:
            await ctx.send(detail_by_role)
    else:
        msg = "Summary shown here. Detailed report sent to your DM channel.\n```{0}```".format(total_table.to_string(index=False))
        await ctx.send(msg)

        # I have to break up different roles. Each Discord message has a server-side hardl imit of 2,000.
        for detail_by_role in detailed_breakdowns:
            msg = "Detailed breakdown: {0} {1}\n".format(item or '', variant or '')
            await ctx.message.author.send(msg + detail_by_role)

async def _user_has_role(user, role_name):
    member = await _map_dm_user_to_member(user)
    return bool(discord.utils.get(member.roles, name=role_name))

@bot.command(
    brief="Admin executing commands on behalf of a user",
    description="Admin executing commands on behalf of a user.")
async def sudo(ctx, member: discord.Member, command: str, *args):
    """
Only admins can execute sudo. 'member' may be @alias (in the inventory room) or 'alias' alone (in DM channels). \
Incorrect spelling of 'alias' will cause the command to fail. Note that 'alias' is case-sensitive.

sudo <maker> add [total] [item] [variant]
sudo <maker> count [total] [item] [variant]
sudo <maker> remove [item] [variant]
sudo <maker> reset [item] [variant]
sudo <collector> collect
sudo <collector> collect add [total] [item] [variant]
sudo <collector> collect count [total] [item] [variant]
sudo <collector> collect remove [item] [variant]
sudo <collector> collect reset [item] [variant]
sudo <collector> collect from <maker> [count] [item] [variant]
sudo <maker> drop <collector> [count] [item] [variant]
"""
    sudo_author = ctx.message.author
    print('Command: sudo {0} {1} {2} ({3})'.format(member, command, args, sudo_author.display_name))

    is_admin = await _user_has_role(sudo_author, ADMIN_ROLE_NAME)
    if not is_admin:
        await ctx.send("❌  You need the admin role to do this. Please ask to be made an admin.")
        return

    if command == 'id':
        # Unadvertised 'sudo Freddie id' - return internal Discord user id
        # Useful for adding admins
        await ctx.send("{0}, # {1}".format(member.id, member.display_name))
        return

    elif command == 'collect':
        if args:
            command = command + ' ' + args[0]
            args = args[1:]

        is_collector = await _user_has_role(member, COLLECTOR_ROLE_NAME)
        if not is_collector:
            await ctx.send("❌  '{0}' needs to have the collector role, for this sudo collect command to work.".format(member))
            raise NotEntitledError()

    if command not in ('count', 'remove', 'add', 'reset',
                       'collect', 'collect count', 'collect remove', 'collect add', 'collect reset', 'collect from',
                       'drop'):
        await ctx.send("❌  command '{0}' not supported by sudo".format(command))
        return

    ctx.message.author = member
    cmd = bot.get_command(command)
    # Note that *args contains ALL strings. Integers will show up as string.
    # This means that commands supported by 'sudo' must do explict conversion of int arguments, and the like.
    await cmd(ctx, *args)

def _get_role_by_name(role_name):
    for role in _get_first_guild().roles:
        if role.name == role_name:
            return role
    raise RuntimeError('Role name "{0}" not found in the associated server/guild'.format(role_name))

@bot.command(
    brief="Find out who is serving what role",
    description="Find out who is serving what role")
async def who(ctx, are: str = None, role: str = None):
    """
The argument [are] is always ignored. It's just there so you can ask:
  who are you - useful for finding zombie bots that haunt this server
  who - same as "who are you"
  who are admins - find out who can run sudo commands, known as botadmins
  who are collectors - find out who collect printed items from makers
"""
    print('Command: who {0} {1} ({2})'.format(are, role, ctx.message.author.display_name))

    if role == 'you' or not role:
        await ctx.send("Count Bot Johnny 5 at your service. ||Run by ({0}) with pid ({1}) V{2}||".format(
            getpass.getuser(), os.getpid(), CODE_VERSION))

    elif role in USER_ROLE_HUMAN_TO_DISCORD_LABEL_MAP:
        role = _get_role_by_name(USER_ROLE_HUMAN_TO_DISCORD_LABEL_MAP[role])
        members = role.members
        names = sorted([member.display_name for member in members])
        output = '  ' + '\n  '.join(names)
        await ctx.send("```{0}```".format(output))

@bot.command(
    brief="Admin instructs an extraneous bot to bow out",
    description="Admin instructs an extraneous bot to bow out.")
async def kamikaze(ctx, pid: int):
    """
Only admins can kill a bot. Use 'who are you' to find the pid of the right bot in the spoiler text.
"""
    sudo_author = ctx.message.author
    print('Command: kamikaze {0} ({1})'.format(pid, sudo_author.display_name))

    is_admin = await _user_has_role(sudo_author, ADMIN_ROLE_NAME)
    if not is_admin:
        await ctx.send("❌  You are not an admin. Please ask to be made an admin first.")
        return

    if os.getpid() == pid:
        await ctx.send("👋  So long, and thanks for all the fish.")
        await bot.close()
    # Do not respond to incorrect PIDs. The point of this command is to kill extraneous bots.
    # Good bots do not need to respond at all.

@bot.command()
async def hello(ctx):
    """Same as 'who are you'"""
    print('Command: hello ({0})'.format(ctx.message.author.display_name))
    cmd = bot.get_command('who')
    await cmd(ctx, 'are', 'you')

@bot.group(
    brief="Tools for collectors to move maker items into collections",
    description="Tools for collectors to move maker items into collections:",
)
async def collect(ctx):
    """
There are subcommands under 'collect'. Type 'help collect' to see these subcommands. \
You can also type 'help collect from' to see help page for a specific subcommand 'from'. \
If you run collect without a subcommand, it shows you your currection collection inventory.
"""
    collect_author = ctx.message.author
    print('Command: collect (group check) ({0})'.format(collect_author.display_name))

    is_collector = await _user_has_role(collect_author, COLLECTOR_ROLE_NAME)
    if not is_collector:
        await ctx.send("❌  You need to have the collector role. Please ask to be made a collector.")
        raise NotEntitledError()

    if ctx.subcommand_passed is None:
        # 'collect' without any subcommand is a 'collect count'
        await _count(ctx, role=USER_ROLE_COLLECTORS)

@collect.command(
    name='count',
    brief="A collector re-counts items in her collection",
    description="A collector recounts items in her collection:")
async def collect_count(ctx, num: int = None, item: str = None, variant: str = None):
    """
This is similar to 'count' that makers use to re-count items they have made. \
With 'collect count', a collect can fix mistakes in previous collections by simply setting a new collection count.

Type 'help count' to see descriptions of [item] and [variant], and how you can use shorter aliases to reference them.

collect count - shows everything in this collector's inventory.
collect count 20 - used when a collector has only one item type in collection.
collect count 20 prusa - used when a collector has only one variant of prusa.
"""
    print('Command: collect count {0} {1} {2} ({3})'.format(num, item, variant, ctx.message.author.display_name))
    await _count(ctx, num, item, variant, role=USER_ROLE_COLLECTORS)

@collect.command(
    name='reset',
    brief="A collector resets item count to 0 in her collection",
    description="A collector resets item count to 0 in her collection:")
async def collect_reset(ctx, item: str = None, variant: str = None):
    """
Reset the count of an item to 0 in a collection. This is basically an alias for 'collect count 0'. \
'Reset' is not the same as 'remove'. Use 'reset' after a drop to a hospital to reset your collection count, \
so you can collect more and update the count later. 'Remove' on the other hand will \
remove an item type in your collection, indicating that you do not plan to collect more of that item type.

collect reset -> collect count 0
collect reset prusa -> collect count 0 prusa
collect reset prusa PETG -> collect count 0 prusa PETG
"""
    print('Command: collect reset {0} {1} ({2})'.format(item, variant, ctx.message.author.display_name))
    await _count(ctx, 0, item, variant, role=USER_ROLE_COLLECTORS)

@collect.command(
    name='remove',
    brief="Remove an item type from a collection",
    description="Remove {item} of {variant} type from collection:")
async def collect_remove(ctx, item: str = None, variant: str = None):
    """
'Remove' is not the same as 'reset'. Use 'remove' when you no longer collect a certain item type.

collect remove - shortcut to remove the only item in your collection.
collect remove [item] - shortcut to update a single variant of an item type.
collect remove all - special command to wipe out all items from collection.
"""
    print('Command: collect remove {0} {1} ({2})'.format(item, variant, ctx.message.author.display_name))
    await _remove(ctx, item, variant, role=USER_ROLE_COLLECTORS)

@collect.command(
    name='add',
    brief="Similar to 'collect count', but it adds instead of updating count",
    description="Add n items to the current collection of item/variant:")
async def collect_add(ctx, num: int = None, item: str = None, variant: str = None):
    """
See 'help count' for descriptions of [item] and [variant], and how you can use shorter aliases to reference them.

collect add - show items you make. Same as 'collect count' without arguments.
collect add 20 - add 20 to the running count of a single item in collection.
collect add 20 prusa - add 20 to the collection of a single variant of prusa.
"""
    print('Command: collect add {0} {1} {2} ({3})'.format(num, item, variant, ctx.message.author.display_name))
    await _count(ctx, num, item, variant, delta=True, role=USER_ROLE_COLLECTORS)

@collect.command(
    name='from',
    brief="A collector moves n items from a maker to her collection",
    description="A collector moves n items from a maker to her collection:")
async def collect_from(ctx, maker: discord.Member, num: int, item: str, variant: str=None):
    """
This is like a banking transfer. A collector transfers n items from a a maker's inventory to the collector's. \
The {maker} can be the collector herself. Everyone can play both roles: makers and collectors at different times \
of the day. The collector word is final. If the maker's inventory is lower than what the collector claims, \
this command will fail.

In the case of discrepancy, a collector should look up the maker's current inventory \
the 'report' command, then transfer the specific number of items. The collector can then make manual corrections \
in her own collection bucket using 'collect add'.

Unlike commands such as 'count' and 'add', this command requires the user to specify both item and variant. \
These values cannot be defaulted.

Type 'help count' to see descriptions of [item] and [variant], and how you can use shorter aliases to reference them.

'maker' may be @alias (in the inventory room) or 'alias' alone (in DM channels).

collect from @Freddie 20 prusa PETG: collector receives 20 items from a maker.
collect from @Freddie -20 prusa PETG: collector returns 20 items back to a maker.
collect from @Freddie 50 earsaver: some items such as ear-savers have no variants
"""
    collector_author = ctx.message.author
    print('Command: collect from {0} {1} {2} {3} ({4})'.format(
        maker, num, item, variant, collector_author.display_name))
    await _collect_from(ctx, maker, num, item, variant)

async def _collect_from(ctx, maker: discord.Member, num: int, item: str, variant: str = None, trial_run_only=False):
    collector_author = ctx.message.author

    if isinstance(maker, str):
        # This is needed for 'sudo' command to invoke this function without the benefit of built-in converters.
        converter= commands.MemberConverter()
        maker_input = maker
        maker = await converter.convert(ctx, maker_input)
        print("converted '{0}' to '{1}'".format(maker_input, maker))

    if isinstance(num, str):
        # This is needed for 'sudo' command to invoke this function without the benefit of built-in convertors.
        num = int(num)

    if num == 0:
        await ctx.send("❌  Collecting 0 items is not a very useful exercise.")
        return

    # Make a trial run to bail out early if args are incorrect, so that we can guarantee the success of the
    # actual transfer which consists of two separate commands, in a pseudo-atomic fashion.
    ctx.message.author = maker
    if None is await _count(ctx, -num, item, variant, delta=True, role=USER_ROLE_MAKERS, trial_run_only=True):
        return
    ctx.message.author = collector_author
    if None is await _count(ctx, num, item, variant, delta=True, role=USER_ROLE_COLLECTORS, trial_run_only=True):
        return

    if trial_run_only:
        return num, item, variant

    ctx.message.author = maker
    await _count(ctx, -num, item, variant, delta=True, role=USER_ROLE_MAKERS)
    ctx.message.author = collector_author
    await _count(ctx, num, item, variant, delta=True, role=USER_ROLE_COLLECTORS)

@bot.command(
    brief="A maker drops items into a collector's drop box",
    description="A maker drops items into a collector's drop box:")
async def drop(ctx, collector: discord.Member, num: str, item: str = None, variant: str = None):
    """
This transfers items out of a maker's inventory, but not quite into a collector's inventory, \
unlike the command 'collect from'. The items are temporarily housed in a collector's drop box. \
Once the collector confirms the drop-off using an OK 👌 reaction, then the bot moves these items \
from the drop box into the collector's inventory.

Type 'help count' to see descriptions of [item] and [variant], and how you can use shorter aliases to reference them.

drop @Katy all ver - drop all variants of Verkstan made into Katy's drop box
drop @Katy all - drop all items of same type in maker's inventory
drop @Katy 20 ver pet - drop only 20 out of current Verkstan PETG inventory
drop @Katy -10 ver pet - take back 10 Verkstans
"""
    maker = ctx.message.author
    print('Command: drop {0} {1} {2} {3} ({4})'.format(collector, num, item, variant, maker.display_name))

    if num == 'all':
        result= await _count(ctx, 0, item, variant, delta=True, role=USER_ROLE_MAKERS, trial_run_only=True)
        if result is None:
            return
        num, _item, _variant = result
    else:
        try:
            num = int(num)
        except:
            await ctx.send("❌  'all' or a number is exepcted. Got '{0}'. See help.".format(num))
            await ctx.send_help(ctx.command)
            return

    if num == 0:
        await ctx.send("❌  Dropping off 0 items is not a very useful exercise.")
        return

    if isinstance(collector, str):
        # This is needed for 'sudo' command to invoke this function without the benefit of built-in converters.
        converter= commands.MemberConverter()
        collector_input = collector
        collector = await converter.convert(ctx, collector_input)
        print("converted '{0}' to '{1}'".format(collector_input, collector))

    is_collector = await _user_has_role(collector, COLLECTOR_ROLE_NAME)
    if not is_collector:
        await ctx.send("❌  '{0}' needs to be a collector for this drop to be successful.".format(collector))
        raise NotEntitledError()

    # Make a trial run to bail out early if args are incorrect, so that we can guarantee the success of the
    # actual transfer which consists of two separate commands, in a pseudo-atomic fashion.
    ctx.message.author = maker
    result = await _count(ctx, -num, item, variant, delta=True, role=USER_ROLE_MAKERS, trial_run_only=True)
    if result is None:
        return
    _current_maker_count, confirmed_item, confirmed_variant = result

    if num >= 0:
        # Let the 'collect from' command check for the validity of this proposed transaction.
        # After all, some collector will eventually need to 'collect' these items.
        ctx.message.author = collector
        if None is await _collect_from(ctx, maker, num, confirmed_item, confirmed_variant, trial_run_only=True):
            return

    # Take current count of dropbox entry for this maker-collector-item-variant combination

    df = INVENTORY_BY_USER_ROLE[USER_ROLE_DROPBOXES]
    maker_user_id = maker.id
    collector_user_id = collector.id

    current_dropped_count = 0
    cond = (df[COL_USER_ID] == maker_user_id) & (df[COL_ITEM] == confirmed_item) & \
           (df[COL_VARIANT] == confirmed_variant) & (df[COL_SECOND_USER_ID] == collector_user_id)
    rows = df[cond]
    if len(rows) == 1:
        row = rows.iloc[0]
        current_dropped_count = row[COL_COUNT]
    new_dropbox_count = num + current_dropped_count

    if new_dropbox_count < 0:
        ctx.message.author = maker
        await ctx.send("❌  Dropbox count would become negative after this operation: '{0}'.".format(new_dropbox_count))
        raise NotEntitledError()

    # -- OK. Let's do it

    # Update maker inventory side of the transaction
    ctx.message.author = maker
    await _count(ctx, -num, confirmed_item, confirmed_variant, delta=True, role=USER_ROLE_MAKERS)

    # Update dropbox side of the transaction
    ctx.message.author = maker
    txt = '{0} {1} {2} {3}'.format(collector.mention, new_dropbox_count, confirmed_item, confirmed_variant)
    await _post_user_record_to_trans_log(ctx, 'drop', txt)

    if new_dropbox_count != 0:
        df.loc[(maker_user_id, confirmed_item, confirmed_variant, collector_user_id)] = \
            [maker_user_id, confirmed_item, confirmed_variant, collector_user_id, new_dropbox_count, datetime.utcnow()]
    else:
        df.drop((maker_user_id, confirmed_item, confirmed_variant, collector_user_id), inplace=True)

    # Only update memory DF after we have persisted the message to the inventory channel.
    msg_prefix = "previous count: {0}  delta: {1}".format(current_dropped_count, num)
    await _send_dropbox_df_as_msg_to_user(ctx, df[(df[COL_USER_ID] == maker_user_id)], prefix=msg_prefix)

if __name__ == '__main__':
    bot.run(get_bot_token())
