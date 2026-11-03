---
name: cabinet-watch
description: Watch #cabinet and answer, as Vic, anyone who pings Vic there. Use for /cabinet-watch, "start the watcher" or "listen in cabinet"; keep it going until told to stop.
---

# Cabinet watcher

Claude sits behind Vic in #cabinet (1155637791917613147). A poller on the VM wakes this session when someone pings
Vic, replies to Vic or says "vic", and Claude answers through Vic's bot token over the REST API. The token never
leaves the VM: `scripts/run.sh` pipes each script over ssh into `venv/bin/python -` in `~/HMS-Victory`, which loads
`.env`. Below, `R` is `/Users/ogme01/Documents/Projects/HMS-Victory/.claude/skills/cabinet-watch/scripts/run.sh`;
write the full path in every command, since shell variables don't carry between Bash calls.

## Start

Run in the background (`run_in_background: true`) and end the turn; the task notification wakes you:

```bash
R watch.py latest
```

The output has `hits` (what woke it), `context_before` (the 30 messages before the first hit), `after_first_hit`
(Vic's own auto-reply shows up here) and `last_id`. After handling it, start it again from the newer of `last_id` and the id of your own post:
`R watch.py <id>`. `"timeout": true` after 6 hours just means start it again.

## Answering

1. **Vic is already typing when you wake.** If you decide not to reply, stop it with `R typing_indicator.py stop`.
   `R typing_indicator.py start` turns it back on for a long job; it gives up by itself after 10 minutes.
2. **Let Vic's auto-reply count.** Vic's chat responder answers pings too. If its answer in `after_first_hit` is
   right, say nothing. Step in only when it's missing, vague or wrong, and say you're correcting it.
3. **Get the whole thread.** A fresh session knows nothing of earlier talk beyond those 30 messages. If the ask
   points further back ("what we said earlier", "the plan above", a reply to an old message), read more with
   `R history.py <oldest_id_you_have> 100` (up to 500) before answering.
4. **Check, don't guess.** Read the live server (roles, channels, overwrites, members) or the databases on the VM
   before stating facts. Read-only GETs are fine; write a throwaway script to the scratchpad and run it with
   `R - < script.py`.
5. **Reply as a Components V2 card** in the style of Vic's dashboard. Posting also stops the typing indicator:

   ```bash
   python3 <scripts>/card.py card.txt reply <message_id> | R -
   ```

   Modes are `reply <id>`, `edit <id>` (turn one of Vic's messages into the card) and `post`. Card file:

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

   Accents: `00247D` info, `E67E22` waiting on a go, `2ECC71` done, `E74C3C` problem. Short bullets, no em-dashes,
   no pings (mentions are switched off, but still write `@​everyone` with a zero-width space).

## Changes

- **Any request to change something gets a confirmation card first** listing exactly what will change, and nothing
  happens until someone says go. That covers roles, permissions, channels, code and config.
- Save the current state before a live server change so it can be put back, and say so in the done card.
- The auto-mode classifier may block live permission edits or deploys even after a go in #cabinet. Don't work
  around it: ask Oggers in this chat, then apply.
- Code changes follow the repo's normal flow: commit, cherry-pick onto `origin/main` in a temp worktree, push, then
  `./update_bot.sh` on the VM.

## Stop

Stop the background task, then run `R stop.py`. Killing the local ssh leaves the remote python running, so this is
what really ends it; it also stops the typing indicator and never touches the bot itself.
