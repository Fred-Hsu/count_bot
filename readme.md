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

If you make ear savers, know that there are no variants. We don't care if you print with PETG or PLA. You can type the full name 'earsaver', or as usual, just the first three letters 'ear':

<pre>
Justin:
<b>count 3 ear</b>

Count Bot:
✅ @Justin: count 3 earsaver
count   item       variant
    3   earsaver
</pre>

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

### Makers marking drop-offs ###

There is a second way for collectors to move items from maker inventories into her collector inventory.
This involves a two-step process, but is actually easier to carry out. It also mimicks real-life actions.

After a maker drops off 4 Verkstan PLA head bands to Nicole, the maker can issue a 'drop' command to 
remove these items off his maker inventory, so he can continue to print and count new head bands.
Type 'help drop' to see details about this command.

<pre>
Freddie:
<b>drop @Nicole 4 verkstan pla</b>

Count Bot:
✅ @Freddie: count 0 verkstan PLA
count   item       variant
   0   verkstan   PLA
✅ @Freddie: drop @Nicole 4 verkstan PLA
count   item       variant   collector
    4   verkstan   PLA       @Nicole  
</pre>

If you usually talk to the Count Bot from a DM channel, you will not be able to rely
on Discord to look up collector's display names using the @ character. DM channels are not associated to a
Discord server such as JCRMRG, so Discord won't help you look up collectors in DM.
But you can use a Discord 'username' in place of a @{server-specific} nickname. 

For instance, @Justin is a JCRMRG-specific nickname. His Discord username is X_g_Z. Note that usernames are 
case sensitive. So you can do this from a DM channel:

<pre>
Freddie:
<b>drop X_g_Z 3 prusa petg</b>
</pre>

[NOT YET IMPLEMENTED] - There will be a way for a collector to claim items in these dropboxes.
That is coming.

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
     item   variant   TOTAL   maker   dropbox  collector
 verkstan       PLA      54      20        10     24
 verkstan      PETG      30       0         0     30
 visor     verkstan      50      50         0      0
 
<b>Makers:</b>

verkstan PLA = 20 TOTAL
  0  Freddie
 20  Vinny

visor verkstan = 50 TOTAL
  50   Nicole

<b>Dropboxes:</b>

verkstan PLA = 10 TOTAL
 4  Freddie     Nicole
 6  Vinny       Nicole

<b>Collectors:</b>

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