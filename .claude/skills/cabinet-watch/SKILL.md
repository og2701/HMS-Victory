---
name: cabinet-watch
description: Watch #cabinet and #bot-workshop and answer, as Vic, anyone who pings Vic there. Use for /cabinet-watch, "start the watcher" or "listen in cabinet"; keep it going until told to stop.
---

# Cabinet watcher

Claude sits behind Vic in #cabinet (1155637791917613147) and #bot-workshop (1141037835445616640). A poller on the
VM wakes this session when someone in either channel pings Vic, replies to Vic or says "vic", and Claude answers through Vic's bot token over the REST API. The token never
leaves the VM: `scripts/run.sh` pipes each script over ssh into `venv/bin/python -` in `~/HMS-Victory`, which loads
`.env`. Below, `R` is `/Users/ogme01/Documents/Projects/HMS-Victory/.claude/skills/cabinet-watch/scripts/run.sh`;
write the full path in every command, since shell variables don't carry between Bash calls.

## Start

Run in the background (`run_in_background: true`) and end the turn; the task notification wakes you:

```bash
R watch.py latest
```

The output has `channel` (`cabinet` or `workshop`, where the first hit was; each hit has its own `channel` too),
`hits` (what woke it), `context_before` (the 30 messages before the first hit), `after_first_hit` (Vic's own
auto-reply shows up here) and `last_id`. Answer in the channel the hit came from: the typing, history and card
scripts below take `cabinet` (the default) or `workshop`.

After handling it, start it again from the newer of `last_id` and the id of your own post: `R watch.py <id>`. `"timeout": true` after 6 hours just means start it again.

## Answering

1. **Vic is already typing when you wake** for a message aimed at Vic (hits have `addresses_vic`); plain chatter
   in pending mode doesn't start it. If you decide not to reply, stop it with `R typing_indicator.py stop`.
   `R typing_indicator.py start <channel>` turns it back on for a long job; it gives up by itself after 10 minutes.
2. **Let Vic's auto-reply count.** Vic's chat responder answers pings too. If its answer in `after_first_hit` is
   right, say nothing. Step in only when it's missing, vague or wrong, and say you're correcting it.
3. **Get the whole thread.** A fresh session knows nothing of earlier talk beyond those 30 messages. If the ask
   points further back ("what we said earlier", "the plan above", a reply to an old message), read more with
   `R history.py <oldest_id_you_have> 100 <channel>` (up to 500) before answering.
4. **Check, don't guess.** Read the live server (roles, channels, overwrites, members) or the databases on the VM
   before stating facts. Read-only GETs are fine; write a throwaway script to the scratchpad and run it with
   `R - < script.py`.
5. **Reply as a Components V2 card** in the style of Vic's dashboard. Posting also stops the typing indicator:

   ```bash
   python3 <scripts>/card.py card.txt reply <message_id> <channel> | R -
   ```

   Modes are `reply <id>`, `edit <id>` (turn one of Vic's messages into the card) and `post`, each followed by
   the channel. Card file:

   ```
   accent: 00247D
   ## 🛂 Title
   One line of summary.
   ===
   ### 🎫 Section
   - point
   ===
   -# small footer
   ```

   Accents: `00247D` info, `E67E22` waiting on a go, `2ECC71` done, `E74C3C` problem. Short bullets, no em-dashes.
   Nobody is pinged by default (still write `@​everyone` with a zero-width space). To ping one person, add a
   `ping: <user_id>` line under the accent and @ them as `<@user_id>` in the text; only that person gets pinged.

6. **React where a card would be too much, and pick the emoji like a regular would.**
   `R react.py <message_id> <emoji> <channel>` adds Vic's reaction and stops the typing indicator (`keep` leaves
   it typing, `remove` takes a reaction back off).
   - **Know the palette.** Run `R emojis.py` once per session: the server's own emojis ranked by how often members
     react with them, each with a message it landed on, then the most used normal emojis. Server emojis go in as
     `name:id`.
   - **Choose for the moment.** Match the message's tone the way members do: a server emoji when one fits (the
     palette's examples show how each is used), otherwise any normal emoji, not only the top of the list. Names
     can mislead, so if unsure what one looks like, download `https://cdn.discordapp.com/emojis/<id>.png` (`.gif`
     when animated) into the scratchpad and look at it first.
   - **When to react:** a thanks, a joke or "nice" aimed at Vic gets a reaction instead of a reply; a long job gets
     a "looking into it" reaction on the request (with `keep`) and a "done" one once the card is up; a go gets an
     approving one before the work starts. One reaction, two at most when they read as a pair.
   - **Never** `:Shut:`, `:bedtime:`, `:oggersglare:`, flags or ❌: they set off Vic's own timeouts, translations
     and deletes. react.py refuses them and the palette leaves them out.

## Changes

- **Any request to change something gets a confirmation card first** listing exactly what will change, and nothing
  happens until someone says go. That covers roles, permissions, channels, code and config.
- **Changes to Vic's code need Oggers' go** (404634271861571584), nobody else's. Anyone can ask for one and get it
  planned, but when someone else asked, the plan card pings Oggers for the final go (`ping: 404634271861571584` and
  `<@404634271861571584>` in the text). A go from anyone else on a code change gets a short reply that it's waiting
  on Oggers, and nothing is built or pushed until Oggers says go. Roles, permissions and other server changes still
  take a go from whoever's running the request in #cabinet.
- While a card waits for a go, run the watcher as `R watch.py <id> pending` (#cabinet) or
  `R watch.py <id> pending:workshop` so any human message in that channel wakes you: people answer "sounds good,
  build it" without pinging Vic. Go back to plain `R watch.py <id>` once it's settled. Chatter
  that isn't a go or a change just means start it again in pending mode.
- Save the current state before a live server change so it can be put back, and say so in the done card.
- The auto-mode classifier may block live permission edits or deploys even after a go in #cabinet. Don't work
  around it: ask Oggers in this chat, then apply.
- Code changes follow the repo's normal flow: commit, cherry-pick onto `origin/main` in a temp worktree, push, then
  `./update_bot.sh` on the VM.

## Stop

Stop the background task, then run `R stop.py`. Killing the local ssh leaves the remote python running, so this is
what really ends it; it also stops the typing indicator and never touches the bot itself.
