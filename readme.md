Discoard bot that keeps count of current numbers of face shields in each person's possession until the next drop.

## 10-minute guide to using Count Bot

The basic idea behind Count Bot is vey simple. Don't be fooled by the number of commands you see when you type 'help'. The basic workflow involves only three commands: **count**, **collect** and **report**. The rest of commands exist to cover rare use cases. 

### Makers ###

Most folks in the group are printing head bands, punching sheets, and supporting pieces. You are makers. 
Each maker has got a box of **items** you have produced. Item types include: **Prusa** head band, 
**Verkstan** head band, **Visor** sheet, etc. Each item has **variants**. For head bands, they are the materials
used, so **PETG** and **PLA**. For visors, they are **prusa** style and **verkstan** style.

Go to the **#bot-inventory** channel, and type: **count**. Count Body will tell you that you have not recorded
any items you make yet. It will also send a help page for the command 'count' to a 

    Freddie     Today at 4:37 PM
    **count**
    
    Count Bot   Today at 4:37 PM
    ❌  You have not recorded any item types yet. See help.



### Collectors ###

### Bot Admins ###



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