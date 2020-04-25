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
import traceback
import getpass

from functools import lru_cache
from discord.ext import commands
from my_tokens import get_bot_token

logging.basicConfig(level=logging.INFO)

INVENTORY_CHANNEL = 'test-sandbox'  # The bot only listens to this text channel, or DM channels
ADMIN_ROLE_NAME = 'botadmin'        # Users who can run 'sudo' commands

# FIXME - use actual Discord roles

ADMINS = {
    700184823628562482,  # Freddie
    396106131845545984,  # justin
    179806659231612928,  # jds2001
    364951250346967040,  # maggotbrain
    695092825368559686,  # timothyjryan
}

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

DEBUG_DISABLE_INVENTORY_POSTS = True  # Leave False for production run. Set to True to debug only in DM channel

ALIAS_MAPS = {}


def _setup_aliases():
    for item, variants in VARIANT_CHOICES.items():
        ALIAS_MAPS.update([(item[:i].lower(), item) for i in range(3, len(item)+1)])
        for variant in variants:
            ALIAS_MAPS.update([(variant[:i].lower(), variant) for i in range(3, len(variant)+1)])


_setup_aliases()


def fake_command_prefix_in_right_channel(_bot, message):
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
    command_prefix=fake_command_prefix_in_right_channel,
    help_command=commands.DefaultHelpCommand(
        no_category='Commands',
        dm_help=False,   # TODO - change this to True to redirect help text that are too long to user's own DM channels?
    ),
)


@bot.listen()
async def on_command_error(ctx, error):
    """
    DiscordPy command failures are horrible. If input argument as much as fail a tpye checking/conversion,
    callstack is printed, and nothing is send back to the user as feedback. Explicitly catch certain types of
    user error here, and provide 'help' feedback instead of silently failing.

    This is a global error handler for all commands. Each command can also provide its own specific error handler.
    """
    if isinstance(error, (commands.errors.BadArgument, commands.errors.MissingRequiredArgument)):
        await ctx.send("❌  I don't completely understand. See help.")
        await ctx.send_help(ctx.command)
    else:
        # If this listener doesn't exist, the Bot.on_command_error does this:
        print('Ignoring exception in command {}:'.format(ctx.command), file=sys.stderr)
        traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)


@bot.event
async def on_ready():
    print('Logged in as')
    print(bot.user.name)
    print(bot.user.id)
    print('------')


@lru_cache()
def get_inventory_channel():
    """Find the right inventory channel model"""
    for ch in bot.get_all_channels():
        if ch.name == INVENTORY_CHANNEL:
            return ch
    raise RuntimeError('No channel named "{0}" found'.format(INVENTORY_CHANNEL))


COL_USER_ID = 'user_id'
COL_ITEM = 'item'
COL_VARIANT = 'variant'
COL_COUNT = 'count'


@lru_cache()
def get_inventory_df():
    """
    Troll through inventory channel's message records to rebuilt our memory inventory dataframe.
    """
    # FIXME - troll through get_inventory_channel() to rebuild the dataframe.
    # Right now it returns only an empty DF.
    # dummy_data = [
    #     ['123', 'prusa', 'PETG', 3],
    # ]
    dummy_data = []

    column_names = [COL_USER_ID, COL_ITEM, COL_VARIANT, COL_COUNT]
    df = pd.DataFrame(dummy_data, columns=column_names)
    return df.set_index(keys=[COL_USER_ID, COL_ITEM, COL_VARIANT], inplace=False, verify_integrity=True, drop=False)


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


async def _post_transaction_log(ctx, trans_text):
    # Only members of associated guilds can post transactions.
    # This is the last line of defense against random users DM'ins the bot to cause DoS attacks.
    if not isinstance(ctx.message.author, discord.Member):
        raise RuntimeError('User "{0}" is not in guild, so cannot post to transaction log'.format(ctx.message.author))

    if ctx.message.channel.type == discord.ChannelType.private:
        await ctx.send("Command processed. Transaction posted to channel '{0}'.".format(INVENTORY_CHANNEL))
        # If private DM channel, also post to inventory channel
        ch = get_inventory_channel()
        if DEBUG_DISABLE_INVENTORY_POSTS:
            await ctx.send('DEBUG: redirecting inventory to this DM: ' + trans_text)
        else:
            await ch.send(trans_text + ' (from DM chat)')
    else:
        await ctx.send("Command processed.")
        await ctx.send(trans_text)


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

count - shortcut to print out items under your possession. Same as 'report [user]'.
count [total] - shortcut to update a single item you own.
count [total] [item] - shortcut to update a single variant of an item type."""

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
    # to show items you have recorded. This usage is equivalent to the "report [user]".
    #
    # count [total]
    # If you only have one item in the system, you can keep updating its current count without re-specifying \
    # the item type and the variant type.
    #
    # count [total] [item]
    # If you only print one variant of an item in the system, you can update its current count without re-specifying \
    # the variant type.

    print('Command: count {0} {1} {2} ({3})'.format(total, item, variant, ctx.message.author.display_name))

    if isinstance(total, str):
        # This is needed for 'sudo' command to invoke this function without the benefit of built-in convertors.
        total = int(total)

    df = get_inventory_df()
    user_id = ctx.message.author.id
    cond = df[COL_USER_ID] == user_id
    found_num = sum(cond)
    if total is None or not item or not variant:
        if not found_num:
            await ctx.send('❌  You have not recorded any item types yet. See help.')
            await ctx.send_help(bot.get_command('count'))
            return
        elif total is None:
            # "count" without argument with existing inventory - same as "report [user]".
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
                        "Or use 'reset count' to remove item types. See help.".format(item))
                    await _send_df_as_msg_to_user(ctx, df[cond])
                    await ctx.send_help(ctx.command)
                    return

    item = await _resolve_item_name(ctx, item)
    if not item:
        return

    variant = await _resolve_item_name(ctx, variant)
    if not variant:
        return

    txt = '{0}: {1} {2} {3}'.format(ctx.message.author.mention, total, item, variant)
    await _post_transaction_log(ctx, txt)

    # Only update memory DF after we have persisted the message to the inventory channel.
    # Think of the inventory channel as "disk", the permanent store.
    # If the bot crashes right here, it can always restore its previous state by trolling through the inventory
    # channel and all DM rooms, to find user commands it has not succesfully processed.
    df.loc[(user_id, item, variant)] = [user_id, item, variant, total]
    await _send_df_as_msg_to_user(ctx, df[(df[COL_USER_ID] == user_id)])


@bot.command(
    brief="Remove an item type from user's record",
    description="Remove {item} of {variant} type from user's record:")
async def remove(ctx, item: str = None, variant: str = None):
    """
Items and variants are case-insensitive. You can also use aliases such as 'ver', 'verk', 'pru', 'pet' \
and 'vis', 'viso', etc. to refer to the the full item and variant names. To see your inventory records,
type 'count'.

remove - shortcut to remove the only item you have in the record.
remove [item] - shortcut to update a single variant of an item type.
remove all - special command to wipe out all records of this user."""

    print('Command: remove {0} {1} ({2})'.format(item, variant, ctx.message.author.display_name))
    df = get_inventory_df()
    user_id = ctx.message.author.id
    cond = df[COL_USER_ID] == user_id
    found_num = sum(cond)

    if not found_num:
        await ctx.send('❌  You have not recorded any item types. There is nothing to remove.')
        return

    if item == 'all':
        txt = '{0}: remove all'.format(ctx.message.author.mention)
        await _post_transaction_log(ctx, txt)

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
    row = df[cond]
    if len(row) != 1:
        txt = '❌  Internal error - got more than one row - this is not expected. Abort.'
        print(txt)
        await ctx.send(txt)
        return

    txt = '{0}: remove {1} {2}'.format(ctx.message.author.mention, item, variant)
    await _post_transaction_log(ctx, txt)

    # Only update memory DF after we have persisted the message to the inventory channel.
    df.drop((user_id, item, variant), inplace=True)
    await _send_df_as_msg_to_user(ctx, df[(df[COL_USER_ID] == user_id)])


async def _map_dm_user_to_member(user):
    # If a 'user' comes from a DM channel, it has a "User" class, not associated to any guild nor roles.
    # Otherwise, user is of "Member" class, with a list of associated roles.
    if isinstance(user, discord.Member):
        return user

    for guild in bot.guilds:
        member = guild.get_member(user.id)
        if member:
            return member
    raise RuntimeError('User "{0}" not a member of my associated guild'.format(user))


async def _map_user_ids_to_display_names(ids):
    # If a user id isn't found to be associated to this guild, it will not be included in the returned map.
    map = {}
    for id in ids:
        for guild in bot.guilds:
            member = guild.get_member(id)
            if member:
                map[id] = member.display_name
                break
    return map


async def _map_user_id_column_to_display_names(df):
    ids = df.user_id.unique()
    map = await _map_user_ids_to_display_names(ids)
    return df.replace(map)


@bot.command(
    brief="Report total inventory in the system",
    description="Report inventory of items by all users, broken down by item, variant and user.")
async def report(ctx, item: str = None, variant: str = None):
    """
'item' and 'variant' are optional. Use them to limit the types of items to report."""

    print('Command: report {0} {1} ({2})'.format(item, variant, ctx.message.author.display_name))

    df = get_inventory_df()
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
    for index, table in groups:
        # Note that 'sparsify' works on all index columns, except for the very last index column.
        ordered = table.sort_index('columns')
        total = ordered[COL_COUNT].sum()
        total_line = "{0} {1} = {2} TOTAL".format(index[0], index[1], total)
        await ctx.send("```{0}\n{1}```".format(total_line, ordered.to_string(index=False)))


async def _user_has_role(user, role_name):
    member = await _map_dm_user_to_member(user)
    return bool(discord.utils.get(member.roles, name=role_name))


@bot.command(
    brief="Admin executing commands on behalf of a user",
    description="Admin executing commands on behalf of a user.")
async def sudo(ctx, member: discord.Member, command: str, *args):
    """
Only admins can execute sudo. 'member' may be @alias (in the inventory room) or 'alias' alone (in DM channels).
Incorrect spelling of 'alias' will cause the command to fail. Note that 'alias' is case-sensitive.

sudo <member> count [total] [item] [variant]
sudo <member> remove [item] [variant]
"""
    sudo_author = ctx.message.author
    print('Command: sudo {0} {1} {2} ({3})'.format(member, command, args, sudo_author.display_name))

    is_admin = await _user_has_role(sudo_author, ADMIN_ROLE_NAME)
    if not is_admin:
        await ctx.send("❌  You are not an admin. Please ask to be made an admin first.".format(command))
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


@bot.command(
    brief="Find out who is serving what role",
    description="Find out who is serving what role")
async def who(ctx, are: str = None, role: str = None):
    """
The argument [are] is always ignored. It's just there so you can ask:
  who are you - useful for finding zombie bots that continue to haunt this server
  who are admins - find out who can run sudo commands, known as botadmins
  who - find out who's who in general
"""
    print('Command: who {0} {1} ({2})'.format(are, role, ctx.message.author.display_name))

    if role == 'you' or not role:
        await ctx.send("Count Bot Johnny 5 at your service. Run by ({0}) with pid ({1})".format(
            getpass.getuser(), os.getpid()))


@bot.command(
    brief="List admins who wield superpower",
    description="List admins who wield superpower.")
async def admins(ctx):
    """
Only admins can execute sudo commands."""

    map = await _map_user_ids_to_display_names(ADMINS)
    names = sorted(map.values())
    output = '  ' + '\n  '.join(names)
    await ctx.send("```{0}```".format(output))

    # FIXME - remove when 'who are admins' is implemented



# FIXME - add a kill command to kill a bot. Must specify the process id of the bot. Only admin can run it

# FIXME - add collectors.
# people should be able to ask who the current collectors are.


bot.run(get_bot_token())
