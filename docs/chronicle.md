# The chronicle

Permanent, never-purged log of everything that happens in the server, kept so the
year-in-review ("wrapped") and any other long-range stats have real data to stand on.

It is **not** `lib/features/message_archive.py`. That one keeps 30 days of message text
in `database.db` purely so bulk deletes can be logged with their content, and purges
itself nightly. The chronicle keeps everything, forever, in its own file.

## Why a separate database

`backup_database` re-zips and re-uploads the whole of `database.db` to `#data-backup`
every five minutes, 288 times a day. The chronicle grows by roughly a gigabyte a year,
so letting it live in that file would make a cheap job more expensive every month for no
extra safety. It also writes constantly and append-only, while the economy tables are
read-modify-write and latency-sensitive; separate files mean separate WALs, so an insert
burst can never hold up a `/pay`. And it is disposable in a way balances are not.

`chronicle.db` is **kept on the instance only**, by decision. At ~2GB (540MB zipped)
after the full backfill it no longer fits the Discord backup path, and off-box storage
was declined. `backup_chronicle` writes one rolling local snapshot nightly at 04:20, to
`chronicle.db.snapshot`, which guards against a corrupted file but not a lost disk. If
the disk goes, the messages can be re-backfilled from Discord; the audit log beyond its
45-day window, and past reactions and voice events, cannot.

## What is recorded

| Table | Rows come from | Notes |
| --- | --- | --- |
| `messages` | `on_message` | Bots included and flagged with `is_bot`. DMs excluded. Deletes set `deleted_ts` in place, so nothing ever disappears. `source` says whether a row came from `live`, `backfill` or `catchup`. |
| `message_edits` | `on_message_edit`, `on_raw_message_edit` | Old and new text per edit; the `messages` row carries the current text. |
| `mentions` | `on_message` | One row per mentioned user/role/channel, plus `@everyone` and the person being replied to. |
| `reactions` | `on_raw_reaction_add/remove`, reactions backfill | Raw events, so uncached messages count too; super reactions flagged `burst`. `source='backfill'` rows are current state read off old messages, with the message's time as `ts`, since Discord doesn't record when a reaction was added. |
| `reaction_counts` | reactions backfill | Discord's own per-emoji totals per message; exact even for reactors who have left. |
| `emoji_uses` | `on_message` | Custom emoji exactly; unicode emoji best-effort by regex. |
| `voice_events` | `on_voice_state_update` | join/leave/move plus mute, deafen, stream and video flips. |
| `member_events` | joins, leaves, bans, `on_member_update` | Nick and username changes, role add/remove, timeouts. |
| `interactions` | `on_interaction` | Slash commands with their options, plus buttons and modals. |
| `audit_log` | `on_audit_log_entry_create` | Kicks, bans, timeouts, channel/role/webhook/emoji edits, pins, thread creation - who did it, to whom, and why. |
| `polls` / `poll_votes` | `on_message`, `on_raw_poll_vote_add/remove` | Question, answers, and every individual vote. |

`messages.msg_type` is Discord's MessageType. Pins, forwards and thread starters carry a
message reference exactly like a reply does, so "is this a reply" is `msg_type = 'reply'`,
not `reply_to IS NOT NULL`.

`messages.flags` carries Discord's own MessageFlags, which is the only way to tell a
voice note from any other audio attachment, or a forward from an ordinary post.

Every recorder swallows its own exceptions. Losing a row of history is acceptable; the
bot falling over because of one is not.

Attachments and voice notes are stored as **Discord CDN URLs and metadata, not bytes**.
Keeping the files themselves would be hundreds of gigabytes; the URLs stay valid while
the message does.

### What cannot be captured

Worth knowing before someone asks why a stat looks thin:

- **Audit log older than 45 days.** Discord itself deletes it. The first backfill rescues
  whatever window is open on the day it runs; everything before that is gone.
- **When an old reaction was added, and reactions later removed.** The reactions
  backfill reads each message's current reactions; Discord keeps no history of them.
- **Reactions, voice events and poll votes while the bot was offline.** They only ever
  exist as gateway events. Messages and audit entries from the gap are recovered
  automatically (see below), and every gap is logged in `outages` so stats over that
  window can be read as undercounts.
- **Attachment and voice note bytes.** Only the CDN URL and metadata are kept; the files
  themselves would be hundreds of gigabytes.
- **DMs, and channels the bot cannot read.**
- **Presence and status changes.** They need the privileged presences intent, which the
  bot does not enable, and the volume dwarfs everything else here.
- **Who invited whom.** Discord does not attribute a join to an invite; getting it means
  snapshotting invite use counts and diffing them on every join. Doable, not built.

### Timestamps

`ts` is UTC epoch seconds everywhere. `messages` also carries `local_day` and
`local_hour`, written in Europe/London at insert time, because SQLite has no timezone
database and "what hour do you post at" has to mean the clock people were looking at.
Group by those columns, never by `strftime(..., 'localtime')`.

## Writing

`lib/chronicle/recorder.py` does cheap attribute reads on the discord.py object, packs
plain tuples and drops them on a bounded queue; one background thread commits them in
batches of up to 2000 (or every second). Order is preserved, which matters: an edit's
`UPDATE` has to land after the `INSERT` of the message it refers to. `chronicle.flush()`
drains the queue and runs on graceful shutdown.

## Restarts and outages

`lib/chronicle/catchup.py` runs on every fresh gateway session: each restart, and any
reconnect that couldn't RESUME (a RESUME replays missed events itself and never reaches
`on_ready`). It runs as a background task, so the bot is fully up while it works.

- **Messages.** READY hands over every channel's `last_message_id` for free, so a
  channel only costs a request when it holds something newer than the newest message we
  have for it - usually a handful, not the thousand in the server. Only messages created
  before this session's READY are fetched; everything after reached `on_message` live.
- **Audit log** entries newer than the newest stored one.
- An **`outages`** row per pass: the gap's start (`last_alive_ts`, stamped on every
  write and on clean shutdown), its end, and what was recovered.

It waits for a running backfill to release its lock rather than put two processes on
one token's rate limit. Rows it adds carry `source = 'catchup'`.

## Backfilling history

The recorder only sees messages from the day it was deployed. Everything older comes
from `scripts/chronicle_backfill.py`, which walks channels newest-to-oldest and stores
its progress per channel, so an interrupted run resumes instead of starting over:

```bash
python scripts/chronicle_backfill.py                    # every readable channel
python scripts/chronicle_backfill.py --channel 123 456  # just these
python scripts/chronicle_backfill.py --after 2026-01-01 # only this year
python scripts/chronicle_backfill.py --threads          # archived threads too
python scripts/chronicle_backfill.py --reactions        # who reacted (very slow)
python scripts/chronicle_backfill.py --concurrency 6    # channels walked in parallel
python scripts/chronicle_backfill.py --no-audit         # skip the 45-day audit sweep
```

Parallelism is across channels, inside one process, on purpose. Discord buckets message
history per channel, so several channels at once is genuinely faster while splitting one
channel into date ranges is not - those pages share a bucket either way. Several
*processes* on one token is worse than useless: each keeps its own idea of the rate
limit, none of them sees the others' 429s, and the global cap is per token, so the usual
end of it is a Cloudflare ban that takes the bot offline too. A lock file enforces one
run at a time.

History pages are 100 messages per request, so a few million messages is a few hours.
`--reactions` costs an extra request per emoji per message, which turns that into days -
hence opt-in. It is safe to run while the bot is live (WAL plus `busy_timeout`), and
inserts are `INSERT OR IGNORE` on `message_id`, so re-running never duplicates.

## Backfilling reactions

`scripts/chronicle_reactions_backfill.py` walks every channel and thread again and, for
each message carrying reactions, asks who is on each emoji - one request per emoji per
reacted message, on top of the history pages. It fills `msg_type` and any missing
messages on the way past, skips reactions the live hook already has, and resumes per
channel from `reaction_progress`. It takes the same lock as the message backfill.

## Reading

`lib/chronicle/queries.py` has the aggregates, with no discord.py in sight so it can run
against a copied file offline:

```python
from lib.chronicle import queries
queries.coverage()                  # what's actually stored - check this first
queries.server_wrapped(2026)        # totals, top posters/channels/emoji, by hour/month
queries.user_wrapped(user_id, 2026) # rank, streak, who they reply to, voice hours
```

`voice_seconds` reconstructs time in voice by pairing each join/move with the next
leave/move, closing anything still open at the end of the window.

## Restoring

Stop the bot, then put the snapshot back in place of the live file:

```bash
sudo systemctl stop hms-victory
rm -f chronicle.db-wal chronicle.db-shm
cp chronicle.db.snapshot chronicle.db
sudo systemctl start hms-victory
```

The catch-up on the next boot refills messages and audit entries from after the
snapshot; reactions and voice events from that window are gone.
