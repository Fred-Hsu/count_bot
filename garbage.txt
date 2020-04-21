import discord
import logging
from my_tokens import _MY_DISCORD_BOT_TOKEN

logging.basicConfig(level=logging.DEBUG)

INVENTORY_CHANNEL = 'test-sandbox'

client = discord.Client()


@client.event
async def on_ready():
    print('We have logged in as {0.user}'.format(client))


@client.event
async def on_message(message):
    print('received message [{0}] from user [{1}]'.format(message.content, message.author))
    if message.author == client.user:
        return

    print('received on channel [{0}]'.format(message.channel))

    if message.content.startswith('help') or message.content.startswith('$help'):
        await message.channel.send('I am just a dumb bot right now. Will get smarter and be useful soon')

    elif message.content.startswith('$send'):
        await message.channel.send('Hello! [{0}] on channel [{1}]'.format(message.author, message.channel))
        await message.channel.send(file=discord.File('test_file.txt'))

    elif message.content.startswith('$list'):
        async for message in message.channel.history(limit=None):
            if message.author == client.user:
                if message.attachments:
                    msg = '  {0}'.format(message.attachments[0].url)
                    await message.channel.send(msg)

client.run(_MY_DISCORD_BOT_TOKEN)
