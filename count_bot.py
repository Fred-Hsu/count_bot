"""
Discoard bot that keeps count of current numbers of face shields in each person's possession until the next drop.

The Count Bot leaches from a Discord server for free storage. This bot does not store anything on the bot host
(AWS or your local desktop). The bot only keeps a in-memory Pandas dataframe of inventory. It keeps this memory
inventory synchronized as along as it lives, with what is stored in Discord. If the bot dies, it can be restarted,
and it will rebuild its memory inventory by reading from Discord.

NOTE: Discord.py isn't available as a Conda package it seems. So it is not specified in meta.yaml. Install directly:
   python -m pip install -U discord.py
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

logging.basicConfig(level=logging.INFO)

INVENTORY_CHANNEL = 'test-sandbox'  # The bot only listens to this text channel, or DM channels
ADMIN_ROLE_NAME = 'botadmin'        # Users who can run 'sudo' commands
COLLECTOR_ROLE_NAME = 'collector'   # Users who collect printed items from makers
PRODUCT_CSV_FILE_NAME = 'product_inventory.csv'  # file name of the product inventory attachment in a sync point

# Items are things that makers can print or build.
ITEM_CHOICES = {
    'verkstan':  "3D Verkstan head band",
    'prusa':     "Prusa head band",
    'visor':     "Transparency sheet",
}

VARIANT_CHOICES = {
    'verkstan':  ["PETG", "PLA"],
    'prusa':     ["PETG", "PLA"],
    'visor':     ["verkstan", "prusa"],
}

# Leave all these debug flags FALSE for production run.
DEBUG_DISABLE_STARTUP_INVENTORY_SYNC = True  # Disable the inventory sync point recorded at bot start-up
DEBUG_DISABLE_INVENTORY_POSTS_FROM_DM = True  # Disable any official inventory posting when testing in DM channel
DEBUG_PRETEND_DM_IS_INVENTORY = True  # Make interactions in DM channel mimic behavior seen in official inventory

ALIAS_MAPS = {}
def _setup_aliases():
    for item, variants in VARIANT_CHOICES.items():
        ALIAS_MAPS.update([(item[:i].lower(), item) for i in range(3, len(item)+1)])
        for variant in variants:
            ALIAS_MAPS.update([(variant[:i].lower(), variant) for i in range(3, len(variant)+1)])
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
    '''Help commands that generate too much output get redirected to your DM channel.''' \
    .format(INVENTORY_CHANNEL)

bot = commands.Bot(
    description=description,
    case_insensitive=True,  # No need to be draconian with case
    command_prefix=_fake_command_prefix_in_right_channel,
    help_command=commands.DefaultHelpCommand(
        no_category='Commands',
        dm_help=True,   # Set to True to redirect help text that are too long to user's own DM channels
    ),
)

class NotEntitledError(commands.errors.CommandError):
    pass

@bot.listen()
async def on_command_error(ctx, error):
    """
    DiscordPy command failures are horrible. If input argument as much as fail a tpye checking/conversion,
    callstack is printed, and nothing is send back to the user as feedback. Explicitly catch certain types of
    user error here, and provide 'help' feedback instead of silently failing.

    This is a global error handler for all commands. Each command can also provide its own specific error handler.
    """
    if isinstance(error, NotEntitledError):
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
    maker_inventory_df.to_csv(s_buf, index=False)
    s_buf.seek(0)
    file = discord.File(s_buf, PRODUCT_CSV_FILE_NAME)

    # FIXME - only write syncpoint if incrementally updated since the last sync point
    sync_text = '✅ ' + "Bot restarted: sync point"

    if DEBUG_DISABLE_STARTUP_INVENTORY_SYNC:

        # FIXME - remove hardcoded user...

        guild = _get_first_guild()
        member = guild.get_member(700184823628562482)
        await member.send('DEBUG: record in DM: ' + sync_text, file=file)
    else:
        ch = _get_inventory_channel()
        await ch.send(sync_text, file=file)

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

@lru_cache()
def _get_inventory_channel():
    """Find the right inventory channel model"""
    for ch in bot.get_all_channels():
        if ch.name == INVENTORY_CHANNEL:
            return ch
    raise RuntimeError('No channel named "{0}" found'.format(INVENTORY_CHANNEL))

COL_USER_ID = 'user_id'
COL_ITEM = 'item'
COL_VARIANT = 'variant'
COL_COUNT = 'count'

maker_inventory_df = pd.DataFrame()  # Stores what makers have made, but not yet passed onto collectors

async def _retrieve_inventory_df_from_transaction_log() -> int:
    """
    Troll through inventory channel's message records to find all relevant transactions until we hit a sync point.
    Use these to rebuild in memory the inventory dataframe.
    """
    ch = _get_inventory_channel()

    # This tracks the last action performed by a user on an item + variant.
    # Only the last action of said tuple is used to rebuild the inventory.
    # Any previous action by the user on said tuple are ignored.
    last_action = {}
    sync_point_df = None

    # Channel history is returned in reverse chronological order.
    # Troll through these entries and process only transaction log-type messages posted by the bot itself.
    async for msg in ch.history():
        text = msg.content

        if msg.author != bot.user:
            continue
        if not text.startswith('✅ '):
            continue

        # FIXME fake sync point for now
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
            sync_point_df = pd.read_csv(csv_mem)
            print("{:60} sync point - stop trolling".format(text))
            break

        if msg.mentions:
            # Messages with mentions are records created in response to a user action
            member = msg.mentions[0]
            head, item, variant = text.rsplit(maxsplit=2)
            _garbage, command_head = head.split(':')
            key = (member.id, item, variant)
            if key in last_action:
                print("{:60} {}".format(text, 'superseded'))
                continue
            else:
                command_head = command_head.strip()
                if command_head.startswith('remove'):
                    last_action[key] = None
                    print("{:60} {}".format(text, command_head))
                elif command_head.startswith('count'):
                    parts = command_head.split()
                    last_action[key] = int(parts[1])
                    print("{:60} {}".format(text, command_head))

    print('  --- updates since last syncpoint --')
    pprint(last_action)
    updates_since_sync_point = len(last_action)

    rows = []
    for (user_id, item, variant), count in last_action.items():
        if count is not None:
            rows.append((user_id, item, variant, count))

    for index, row in sync_point_df.iterrows():
        key = (row[COL_USER_ID], row[COL_ITEM], row[COL_VARIANT])
        if key not in last_action:
            rows.append((row[COL_USER_ID], row[COL_ITEM], row[COL_VARIANT], row[COL_COUNT]))

    print('  --- rebuilt inventory --')
    pprint(rows)

    column_names = [COL_USER_ID, COL_ITEM, COL_VARIANT, COL_COUNT]
    df = pd.DataFrame(rows, columns=column_names)
    df.set_index(keys=[COL_USER_ID, COL_ITEM, COL_VARIANT], inplace=True, verify_integrity=True, drop=False)

    global maker_inventory_df
    maker_inventory_df = df
    return updates_since_sync_point

async def _send_df_as_msg_to_user(ctx, df):
    if not len(df):
        await ctx.send("```(no inventory records)```")
    else:
        result = df.loc[:, [COL_COUNT, COL_ITEM, COL_VARIANT]]
        await ctx.send("```{0}```".format(result.to_string(index=False)))

async def _resolve_item_name(ctx, item):
    item_name = ALIAS_MAPS.get(item.lower())
    if not item_name:
        await ctx.send("❌  Item '{0}' is not something I know about. See help.".format(item))
        await ctx.send_help(ctx.command)
        return None
    return item_name

async def _resolve_variant_name(ctx, variant):
    variant_name = ALIAS_MAPS.get(variant.lower())
    if not variant_name:
        await ctx.send("❌  Variant '{0}' is not something I know about. See help.".format(variant))
        await ctx.send_help(ctx.command)
        return None
    return variant_name

async def _post_user_count_to_trans_log(ctx, trans_text):
    """
    All valid transactions must begin with '✅ '.
    Do not post transaction messages without calling this function.
    """

    # Only members of associated guilds can post transactions.
    # This is the last line of defense against random users DM'ins the bot to cause DoS attacks.
    # The function will raise exception of user is not in the guild.
    await _map_dm_user_to_member(ctx.message.author)

    if ctx.message.channel.type == discord.ChannelType.private:
        await ctx.send("Command processed. Transaction posted to channel '{0}'.".format(INVENTORY_CHANNEL))
        # If private DM channel, also post to inventory channel
        ch = _get_inventory_channel()
        if DEBUG_DISABLE_INVENTORY_POSTS_FROM_DM:
            await ctx.send('DEBUG: record in DM: ✅ ' + trans_text)
        else:
            await ch.send('✅ ' + trans_text + ' (from DM chat)')
    else:
        await ctx.send("Command processed.")
        await ctx.send('✅ ' + trans_text)

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

async def _count(ctx, total: int = None, item: str = None, variant: str = None, delta: bool = False):
    """Internal implementation of count, add and reset."""

    if isinstance(total, str):
        # This is needed for 'sudo' command to invoke this function without the benefit of built-in convertors.
        total = int(total)

    df = maker_inventory_df
    user_id = ctx.message.author.id
    cond = df[COL_USER_ID] == user_id
    found_num = sum(cond)
    if total is None or not item or not variant:
        if not found_num:
            await ctx.send('❌  You have not recorded any item types yet. See help.')
            await ctx.send_help(ctx.command)
            return
        elif total is None:
            # "count" without argument with existing inventory.
            result = df[cond]
            await _send_df_as_msg_to_user(ctx, result)
            return
        else:
            # The user is updating count without fully specifying both item and variant args.
            # Either item is not provided, or both item and variant are not provided.
            # Do a search to see if it is possible to narrow down recorded items to just one item.
            if not item or not variant:
                if item:
                    # Variant is not specified.
                    item = await _resolve_item_name(ctx, item)
                    if not item:
                        return

                    cond = (df[COL_USER_ID] == user_id) & (df[COL_ITEM] == item)
                    found_num = sum(cond)
                    # Fall through

                # No item nor variant are specified.
                if found_num == 1:
                    # There is only one row in the record. Retrieve item and variant names from the single record.
                    row = df[cond]
                    item = row[COL_ITEM][0]
                    variant = row[COL_VARIANT][0]
                    # Fall through to normal code which updates the count
                else:
                    await ctx.send("❌  Found more than one types of items. Please be more specific. "
                        "Or use 'reset' to remove item types. See help.".format(item))
                    await _send_df_as_msg_to_user(ctx, df[cond])
                    await ctx.send_help(ctx.command)
                    return

    item = await _resolve_item_name(ctx, item)
    if not item:
        return

    variant = await _resolve_item_name(ctx, variant)
    if not variant:
        return

    if delta:
        # this is not an update of current count, but a delta addition to current count.
        cond = (df[COL_USER_ID] == user_id) & (df[COL_ITEM] == item) & (df[COL_VARIANT] == variant)
        rows = df[cond]
        if len(rows) == 1:
            row = rows.iloc[0]
            total += row[COL_COUNT]

    txt = '{0}: count {1} {2} {3}'.format(ctx.message.author.mention, total, item, variant)
    await _post_user_count_to_trans_log(ctx, txt)

    # Only update memory DF after we have persisted the message to the inventory channel.
    # Think of the inventory channel as "disk", the permanent store.
    # If the bot crashes right here, it can always restore its previous state by trolling through the inventory
    # channel and all DM rooms, to find user commands it has not succesfully processed.
    df.loc[(user_id, item, variant)] = [user_id, item, variant, total]
    await _send_df_as_msg_to_user(ctx, df[(df[COL_USER_ID] == user_id)])

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
    brief="Remove an item type from user's record",
    description="Remove {item} of {variant} type from user's record:")
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
    df = maker_inventory_df
    user_id = ctx.message.author.id
    cond = df[COL_USER_ID] == user_id
    found_num = sum(cond)

    if not found_num:
        await ctx.send('❌  You have not recorded any item types. There is nothing to remove.')
        return

    if item == 'all':
        txt = '{0}: remove all'.format(ctx.message.author.mention)
        await _post_user_count_to_trans_log(ctx, txt)

        # Only update memory DF after we have persisted the message to the inventory channel.
        df.drop((user_id), inplace=True)
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
            await ctx.send("❌  Found more than one types of items. Please be more specific. See help.".format(item))
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
        else:
            await ctx.send("❌  Found more than one variant of items. Please be more specific. See help.".format(item))
            await _send_df_as_msg_to_user(ctx, df[cond])
            await ctx.send_help(ctx.command)
            return

    item = await _resolve_item_name(ctx, item)
    if not item:
        return

    variant = await _resolve_item_name(ctx, variant)
    if not variant:
        return

    cond = (df[COL_USER_ID] == user_id) & (df[COL_ITEM] == item) & (df[COL_VARIANT] == variant)
    rows = df[cond]
    if len(rows) != 1:
        txt = '❌  Internal error - got more than one row - this is not expected. Abort.'
        print(txt)
        await ctx.send(txt)
        return

    txt = '{0}: remove {1} {2}'.format(ctx.message.author.mention, item, variant)
    await _post_user_count_to_trans_log(ctx, txt)

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

async def _map_user_ids_to_display_names(ids):
    # If a user id isn't found to be associated to this guild, it will not be included in the returned map.
    guild = _get_first_guild()
    mapped = {}
    for id in ids:
        member = guild.get_member(id)
        if member:
            mapped[id] = member.display_name
    return mapped

async def _map_user_id_column_to_display_names(df):
    ids = df.user_id.unique()
    mapped = await _map_user_ids_to_display_names(ids)
    return df.replace(mapped)

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

    df = maker_inventory_df
    if not len(df):
        await ctx.send('There are no records in the system yet.')
        return

    if item:
        item = await _resolve_item_name(ctx, item)
        if not item:
            return
        df = df[df[COL_ITEM] == item]

    if variant:
        variant = await _resolve_item_name(ctx, variant)
        if not variant:
            return
        df = df[df[COL_VARIANT] == variant]

    if not len(df):
        await ctx.send('No records found for specified item/variant')
        return

    mapped = await _map_user_id_column_to_display_names(df)

    renamed = mapped.rename(columns={COL_USER_ID: "user"})
    repivoted = renamed.set_index(keys=[COL_ITEM, COL_VARIANT], drop=True)
    groups = repivoted.groupby([COL_ITEM, COL_VARIANT], sort=True)

    for_DM = []
    for_inventory = []
    for index, table in groups:
        # Note that 'sparsify' works on all index columns, except for the very last index column.
        ordered = table.sort_index('columns')
        total = ordered[COL_COUNT].sum()
        total_line = "{0} {1} = {2} TOTAL".format(index[0], index[1], total)

        for_DM.append("```{0}\n{1}```".format(total_line, ordered.to_string(index=False, header=False)))
        for_inventory.append(total_line)

    if ctx.message.channel.type == discord.ChannelType.private and not DEBUG_PRETEND_DM_IS_INVENTORY:
        for txt in for_DM:
            await ctx.send(txt)
    else:
        await ctx.send('Summary shown here. Detailed report sent to your DM channel.')
        await ctx.send("```{0}```".format('\n'.join(for_inventory)))
        await ctx.message.author.send("Detailed report: {0} {1}".format(item or '', variant or ''))
        for txt in for_DM:
            await ctx.message.author.send(txt)

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

sudo <member> count [total] [item] [variant]
sudo <member> remove [item] [variant]
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

    if command not in ('count', 'remove'):
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

    role_map = {
        'admins':       ADMIN_ROLE_NAME,
        'collectors':   COLLECTOR_ROLE_NAME,
    }

    if role == 'you' or not role:
        await ctx.send("Count Bot Johnny 5 at your service. ||Run by ({0}) with pid ({1})||".format(
            getpass.getuser(), os.getpid()))

    elif role in role_map:
        role = _get_role_by_name(role_map[role])
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
    if ctx.invoked_subcommand is None:
        await ctx.send("Command not yet implemented...")

    is_collector = await _user_has_role(collect_author, COLLECTOR_ROLE_NAME)
    if not is_collector:
        await ctx.send("❌  You need to have the collector role. Please ask to be made a collector.")
        raise NotEntitledError()

    print('Command: collect ... ({0})'.format(collect_author.display_name))

    # FIXME implement 'collect' without argument - print out the collection table for this user

@collect.command(
    name='from',
    brief="A collector moves n items from a maker to her collection",
    description="A collector moves n items from a maker to her collection:")
async def collect_from(ctx, maker: discord.Member, num: int = None, item: str = None, variant: str = None):
    """
This is like a banking transfer. A collector transfers n items from a a maker's inventory to the collector's. \
The {maker} can be the collector herself. Everyone can play both roles: makers and collectors at different times \
of the day. The collector word is final. If the maker's inventory is lower than what the collector claims, \
the maker's inventory count is simply reduced to 0.

Type 'help count' to see descriptions of [item] and [variant], and how you can use shorter aliases to reference them.

collect from @Freddie 20: used when Freddie only makes one exact item type.
collect from @Freddie 20 prusa: when Freedie makes only one variant of prusa.
collect from @Freddie -20 prusa PETG: collector returns 20 items back to a maker.
"""
    print('Command: collect from {0} {1} {2} {3} ({4})'.format(maker, num, item, variant, ctx.message.author.display_name))

    await ctx.send("Command not yet implemented...")
    # FIXME implement 'collect from'
    # await _count(ctx, num, item, variant, delta=True)

@collect.command(
    name='count',
    brief="A collector re-counts items in her collection",
    description="A collector recounts items in her collection:")
async def collect_count(ctx, num: int = None, item: str = None, variant: str = None):
    """
This is similar to 'count' that makers use to re-count items they have made. \
With 'collect count', a collect can fix mistakes in previous collections by simply setting a new collection count.

Type 'help count' to see descriptions of [item] and [variant], and how you can use shorter aliases to reference them.

collect count 20 - used when a collector has only one item type in collection.
collect count 20 prusa - used when a collector has only one variant of prusa.
"""
    print('Command: collect count {0} {1} {2} ({3})'.format(num, item, variant, ctx.message.author.display_name))

    await ctx.send("Command not yet implemented...")
    # FIXME implement 'collect count'

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

    await ctx.send("Command not yet implemented...")
    # FIXME implement 'collect reset'
    # await _count(ctx, 0, item, variant)

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

    await ctx.send("Command not yet implemented...")
    # FIXME implement 'collect add'
    # await _count(ctx, num, item, variant, delta=True)

# FIXME - people should be able to ask who the current collectors are.

bot.run(get_bot_token())
