Discoard bot that keeps count of current numbers of face shields in each person's possession until the next drop.

## 10-minute guide to using Count Bot

The basic idea behind Count Bot is vey simple. Don't be fooled by the number of commands you see when you type 'help'. The basic workflow involves only three commands: **count**, **collect** and **report**. The rest of commands exist to cover rare use cases. 

### For makers ###

Most folks in the group are printing head bands, punching sheets, and supporting pieces. You are makers. 
Each maker has got a box of **items** you have produced. Item types include: **Prusa** head band, 
**Verkstan** head band, **Visor** sheet, etc. Each item has **variants**. For head bands, they are the materials
used, so **PETG** and **PLA**. For visors, they are **prusa** style and **verkstan** style.

Go to the **#bot-inventory** channel, and type: **count**. Count Body will tell you that you have not recorded
any items you make yet. It will also send a help page for the command 'count' to a 

<pre>
Freddie:
<b>count</b>

Count Bot:
❌  You have not recorded any item types yet. See help.
</pre>

Tell Countbot about the item you make. Most makers make only one item/variant. Let's say that you print 
Verkstan head bands with PLA, and you currently have 12 printed. 

<pre>
Freddie:
<b>count 12 verkstan pla</b>

Count Bot:
✅ @Freddie: count 12 verkstan PLA
count   item       variant
   12   verkstan   PLA
</pre>

There is no need to capitalize anything, but you can if you insist. You also od not need to spell out the full
word 'verkstan'. The bot knows what you mean if you give him at least three letters. So, 'ver', 'verks' and 'verkst',
will all work. Say, you printed more head bands. Now you count 24.

<pre>
Freddie:
<b>count 24</b>

Count Bot:
✅ @Freddie: count 24 verkstan PLA
count   item       variant
   24   verkstan   PLA
</pre>

That's it. Just keep updating your total count as you print more. Perhaps once a day. If you only make one 
type of item, you don't even need to tell Count Bot what you make anymore. Just keep updating that one current count.
Now, if you make more than one item or variant types, then you need to be more specific. But that's for another
longer guide. Try typing '**help count**', and see what Count Bot tells you. 

### Public inventory channel vs private DM channel

You can talk to Count Bot from either the public inventory channel **#bot-inventory**, or from your private
**DM** (direct message) channel. Count Bot in fact will redirect some lengthy replies to your DM channel, 
even when you talk to it in the public channel.

However, Count Bot will always leave a record in the public channel, whenever you make a counting transaction,
as you have done earlier. All records are marked with a green checkmark, ✅. This lets the rest of the team
know about your progress. And the bot in fact uses these records as its permanent database. 

### Collectors ###

Collectors are folks who collect printed head bands and the rest from makers. Collectors then make delivery trips
to hospitals and other healthcare organizations. Collectors have a Discord role of '**collector**' formally.
Only users with that role may issue collector commands shown here. 

Say, you are a collector (Nicole). And Freddie just dropped off 24 verkstan head bands. You can transfer 
these 24 items from Freddie's box to your collector box. This leaves Freddie with 0 Verkstan head band in his 
maker box.

<pre>
Nicole:
<b>collect from @Freddie 24 ver pla</b>

Count Bot:
✅ @Nicole: collect count 24 verkstan PLA
 count  item       variant
    24  verkstan   PLA
    50  verkstan   PETG
 
✅ @Freddie: count 0 verkstan PLA
 count  item       variant
     0  verkstan   PLA
</pre>

### Find out who has what

Every collector may also have her own maker box. When playing the maker role, a collector can check her 
maker box by issuing a single word: **count**. So Nicole is punching Verkstan visor sheets.

<pre>
Nicole:
<b>count</b>

Count Bot:
 count  item    variant
    50  visor   verkstan
</pre>

Similarly, she can check her collector box by issuing a single word: **collect**.

<pre>
Nicole:
<b>collect</b>

Count Bot:
 count  item       variant
    24  verkstan   PLA
    30  verkstan   PETG
</pre>

Anyone can ask Count Bot to spit out a report on the current state of the inventory. 
Just ask for: **report**.

<pre>
Freddie:
<b>report</b>

Count Bot:
<b>Summary:</b>
     item   variant   TOTAL   maker   collector
 verkstan       PLA      44      20          24
 verkstan      PETG      30       0          30
 visor     verkstan      50      50           0
 
<b>Makers</b>

verkstan PLA = 20 TOTAL
  0  Freddie
 20  Vinny

visor verkstan = 50 TOTAL
  50   Nicole

<b>Collectors</b>

verkstan PLA = 24 TOTAL
 24  Nicole

verkstan PETG = 30 TOTAL
 30  Nicole
</pre>

## The rest of user guide

(to be written)

### Getting help

(to be written)

### Bot Admins ###

(to be written)

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