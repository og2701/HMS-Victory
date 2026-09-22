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

`chronicle.db` is backed up **daily at 04:20** instead (`backup_chronicle`), as
`chronicle_backup_<timestamp>.zip` parts in the same channel. Past 200MB zipped the
upload is skipped with a warning: at that point it wants off-box storage, not Discord.

## What is recorded

| Table | Rows come from | Notes |
| --- | --- | --- |
| `messages` | `on_message` | Bots included and flagged with `is_bot`. DMs excluded. Deletes set `deleted_ts` in place, so nothing ever disappears. |
| `message_edits` | `on_message_edit`, `on_raw_message_edit` | Old and new text per edit; the `messages` row carries the current text. |
| `mentions` | `on_message` | One row per mentioned user/role/channel, plus `@everyone` and the person being replied to. |
| `reactions` | `on_raw_reaction_add/remove` | Raw events, so uncached messages count too. `author_id` is filled when the message was cached. |
| `emoji_uses` | `on_message` | Custom emoji exactly; unicode emoji best-effort by regex. |
| `voice_events` | `on_voice_state_update` | join/leave/move plus mute, deafen, stream and video flips. |
| `member_events` | joins, leaves, bans, `on_member_update` | Nick and username changes, role add/remove, timeouts. |
| `interactions` | `on_interaction` | Slash commands with their options, plus buttons and modals. |

Every recorder swallows its own exceptions. Losing a row of history is acceptable; the
bot falling over because of one is not.

Attachments and voice notes are stored as **Discord CDN URLs and metadata, not bytes**.
Keeping the files themselves would be hundreds of gigabytes; the URLs stay valid while
the message does.

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
```

History pages are 100 messages per request, so a few million messages is a few hours.
`--reactions` costs an extra request per emoji per message, which turns that into days -
hence opt-in. It is safe to run while the bot is live (WAL plus `busy_timeout`), and
inserts are `INSERT OR IGNORE` on `message_id`, so re-running never duplicates.

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

Download every `chronicle_backup_<timestamp>` part from `#data-backup`, concatenate them
in order, unzip, and drop the resulting `chronicle.db` next to `main.py`:

```bash
cat chronicle_backup_*_part*.zip > chronicle.zip && unzip chronicle.zip
```
