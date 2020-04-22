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
import traceback

from functools import lru_cache
from discord.ext import commands
from my_tokens import get_bot_token

logging.basicConfig(level=logging.INFO)

INVENTORY_CHANNEL = 'test-sandbox'  # The bot only listens to this text channel, or DM channels

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
    if isinstance(error, commands.errors.BadArgument):
        await ctx.send("I don't completely understand. Please see help doc:")
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


@bot.command(
    brief="Update the current count of items from a maker",
    description='Update the current {total} count of an {item} of {variant} type from a maker:')
async def count(ctx, total: int = None, item: str = None, variant: str = None):
    """
    Item and variant choices are shown below. Words are case-insensitive.
    You can also use aliases such as 'ver', 'verk', 'pru', 'pet'
    and 'vis', 'viso', etc. to refer to the the full item and variant names.

    Item        variant
    --------------------
    verkstan    PETG
    verkstan    PLA
    prusa       PETG
    prusa       PLA
    visor       verkstan
    visor       prusa

    If you have already recorded items in the system before, you can use "count" without any arguments
    to show items you have recorded. This usage is equivalent to the "show" command without arguments.
    """
    df = get_inventory_df()
    print( 'count command - current DF:')
    print(df)

    user_id = ctx.message.author.id
    cond = df[COL_USER_ID] == user_id
    if total is None or not item or not variant:
        if not sum(cond):
            await ctx.send('You have not recorded any item types yet. Please see the "help count" doc:')
            await ctx.send_help(bot.get_command('count'))
            return
        else:
            # "count" without argument with existing inventory - same as "show" without args
            result = df[cond]
            result = result.loc[:, [COL_COUNT, COL_ITEM, COL_VARIANT]]
            await ctx.send("```{0}```".format(result.to_string(index=False)))
            return

    item_name = ALIAS_MAPS.get(item.lower())
    if not item_name:
        await ctx.send("Item '{0}' in not something I know about.".format(item))
        await ctx.send_help(ctx.command)
        return

    variant_name = ALIAS_MAPS.get(variant.lower())
    if not variant_name:
        await ctx.send("Variant '{0}' in not something I know about.".format(variant))
        await ctx.send_help(ctx.command)
        return

    txt = '{0}: {1} {2} {3}'.format(ctx.message.author.mention, total, item_name, variant_name)
    await ctx.send(txt)
    # If private DM channel, also post to inventory channel
    if ctx.message.channel.type == discord.ChannelType.private:
        ch = get_inventory_channel()

        if DEBUG_DISABLE_INVENTORY_POSTS:
            await ctx.send('DEBUG: inventory channel not posted')
        else:
            await ch.send(txt + ' (from DM chat)')

    # Only update memory DF after we have persisted the message to the inventory channel.
    # Think of the inventory channel as "disk", the permanent store.
    # If the bot crashes right here, it can always restore its previous state by trolling through the inventory
    # channel and all DM rooms, to find user commands it has not succesfully processed.
    df.loc[(user_id, item_name, variant_name)] = [user_id, item_name, variant_name, total]


bot.run(get_bot_token())
