Discoard bot that keeps count of current numbers of face shields in each person's possession until the next drop.

## How to use Count Bot

The basic idea behind Count Bot is vey simple. Don't be fooled by the number of commands you see when you type 'help'. The basic workflow involves only three commands: **count**, **collect** and **report**. The rest of commands exist to cover rare use cases.





## How to deploy Count Bot

To create a Discord Bot, see this: https://discordpy.readthedocs.io/en/latest/discord.html

Give the bot these permissions:

* VIEW_CHANNEL
* SEND_MESSAGES
* MANAGE_MESSAGES
* EMBED_LINKS
* READ_MESSAGE_HISTORY

To invite this bot into a Discord server (guild):

* Create a channel for the bot to monitor. Call it, for instance **#bot-inventory**.

* Create a role for users who can issue 'sudo' commands to the bot. Call it, for instance **botadmin**.

* Create a role for users who can collect printed items from other makers. Call it, for instance **collector**.

Get a bot invitation URL from the OAuth2 tab of the bot page. Click on it to invite the bot into this server.

* Choose a server
* You will be asked to confirm roles. Leave the 5 roles you chose earlier as they are. 

Run the bot code somewhere, on your desktop, on AWS, etc.

* Set up configuration variables such as bot token, inventory channel name, role names, etc. See code for how to do this, for now. This will be enhanced in the future.