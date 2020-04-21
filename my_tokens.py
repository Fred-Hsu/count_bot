import os
import json

__all__ = {
    "get_bot_token",
}


def is_configured():
    """
    If the configuration file is not found, then this bot code is being run in a new environment.
    We do not check in the configuration file into git. The configuration file contains tokens
    that must be shared outside the code.
    """
    return os.path.isfile('_discord_config_no_commit.txt')


def get_bot_token():
    if not is_configured():
        print('This bot is being run in a new production environment')
        print('Please supply discord bot token')
        bot_token = input('Bot token ID (Will be stored in plaintext in config file):')
        f = open('_discord_config_no_commit.txt', 'w')
        f.write(str(json.dumps({'TOKEN': bot_token})) + "\n")
        f.close()
    else:
        f = open('_discord_config_no_commit.txt', 'r')
        first = f.readline()
        bot_token = json.loads(first.replace("\\n", ""))['TOKEN']
        f.close()
    return bot_token
