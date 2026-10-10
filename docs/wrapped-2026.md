# UKPlace Wrapped 2026

Plan for the end-of-year video: a portrait (9:16), roughly three-minute story of the server's
year, built from `chronicle.db` and `database.db`, rendered frame-exact the way the Big Brother
replay was.

This repo is public, so this file holds the plan and the ideas only. Anything with member names
attached (the first-pass findings, who won what) lives locally in `scratch/wrapped-2026/`, which
is gitignored.

## Last year's video (2025)

A two-minute portrait story with a segmented progress bar along the top, set to music, every
slide a red or blue gradient with the same bold type. In order:

1. Title card with the server icon.
2. Total messages ("across 350 days").
3. When you talk: activity by hour.
4. Busiest day.
5. Top 10 channels.
6. Community Awards, four grids of four: Night Owl, Early Bird, The Waffler (longest average
   message), Silent Type (shortest), The Shouter (most ALL CAPS), Nosey Parker (most
   questions), Hype Machine (gave most reactions), Local Celeb (mentioned most), Media Mogul
   (most files), Emoji Artist, The Gossip (mentioned others most), The Plug (most links),
   The Replier, Crowd Pleaser (got most reactions), The Tourist (most channels), The
   Committed (most active days).
7. Iconic Duo (interactions both ways) and It's Complicated (one-way mentions).
8. Biggest Fan (reactions one way) and Proper Mates (reactions both ways).
9. Hall of Fame, about six slides of starred messages.
10. Top emoji, top reactions.
11. Top talkers, reaction leaders.
12. "Thank you" over a wall of member avatars.

**Keep:** the awards grid (the best part, so double it), the duo and fan slides, Hall of Fame,
the emoji slides and the avatar wall ending.

**Fix:**

- The hour chart was in UTC. Use `local_hour`, which is UK time.
- Hall of Fame took about 30 of the 120 seconds, and one slide held a full-screen essay for six
  seconds that nobody could read. Five or six short messages, one per beat.
- Most lists sat still for 4-6 seconds. Count numbers up and reveal #1 last.
- Last year's numbers don't match the chronicle (it has 2025's busiest day as 27 January, not
  9 July, and more messages overall), so they came from a thinner source. Every comparison
  with 2025 should be recomputed from the chronicle, never read off last year's video.

## What the data covers

| Source | Covers 2026 from | Notes |
| --- | --- | --- |
| `messages`, `mentions`, `emoji_uses`, `message_edits` | 1 Jan (backfilled) | Also all of 2025, so every stat can be compared year on year. |
| `reactions`, `reaction_counts` | 1 Jan (backfilled) | Backfilled reactions carry the message's time, not when the reaction was added. |
| `voice_events`, `interactions`, `member_events` | 22 Sep | Recorder went live then. Cannot be recovered earlier. |
| `audit_log` | 17 Aug | Discord only keeps 45 days. |
| Deleted messages | 22 Sep | The backfill can't see messages that were already gone. |
| `daily_summaries`, `balance_snapshots/` | 19 Jan | Joins, leaves, bans and boosts per day; one full balance dump per day. |
| `user_transactions` | all year | Every UKPence movement with a reason. Pruned at 400 days, so extract by early February 2027. |
| `casino_results` | 2 Jun | Poker isn't in it; only in `user_transactions`. |
| `pvp_results`, `kart_*`, `climb_*`, `spitfire_*`, `paperboy_*`, `pay_transfers`, `shop_purchases`, `lottery_*`, `bb_*` | when each feature launched | Full history. |
| `xp`, Wordle, crossword, `skyrim_profiles.json` | current state only | See gaps. |

## Gaps to fix before December

1. **XP has no history.** `xp` is overwritten. Snapshot it daily from now so October to
   December gains and level-ups can be shown.
2. **Wordle and crossword wipe each day.** Log each day's results to a table. Rewarded solves
   in `user_transactions` give partial history before that.
3. **Sold county balls are deleted**, so "caught this year" undercounts. Soft-delete instead.
4. **Skyrim keeps lifetime totals only.** Fine this year since it's new, but snapshot it on
   31 December so 2027 can be diffed.
5. **Voice, slash commands and joins via the gateway** only start on 22 September. Nothing to
   do but keep recording; those slides say "since September" or get cut.

## Chapters (draft)

1. **Cold open and title.**
2. **The year in numbers**, each with its change on 2025, including the date 2026 passed all of
   2025's messages.
3. **The calendar:** month by month with the spikes labelled, a contributions-style heatmap of
   every day, busiest day, busiest weekday, posting by hour in UK time.
4. **Channels:** top 10, the riser and the faller against 2025, channels born this year.
5. **Community Awards:** last year's 16 plus the new ones below.
6. **People:** most replied-to, iconic duos, one-way traffic, biggest fans, rivals, and a
   friend-group map drawn from the reply graph.
7. **Hall of Fame:** five or six short ones.
8. **Words and emoji:** top emoji and reactions, words of each month, the laugh index.
9. **Casino.**
10. **UKPence.**
11. **Games:** Kart, the daily games, Skyrim, county balls, badges.
12. **Big Brother**, pointing at the full replay.
13. **Server life:** members in and out, boosts, new emoji, the Halloween icons.
14. **Thank you** over the avatar wall.

## Award catalogue

Last year's 16 stay. New for 2026:

| Award | Stat | Source |
| --- | --- | --- |
| High Roller | most UKPence staked | `casino_results` |
| Lucky Sod | biggest single casino win | `casino_results` |
| The House's Best Friend | biggest net casino loss | `casino_results` |
| Philanthropist | most UKPence sent with /pay | `pay_transfers` |
| Rookie of the Year | most messages from someone whose first ever message was in 2026 | `messages` |
| Glow-up | biggest rise in messages on 2025 | `messages` |
| The Welcomer | most new members welcomed | `user_transactions` |
| The Bot's Best Friend | most replies to HMS Victory | `messages` |
| Voice Note Royalty | most voice notes | `messages.flags` (8192) |
| The Most British | innit, mate, proper, cheers, bruv per message | `messages` |
| The Apologist | most "sorry" | `messages` |
| The Glue | talked to the most different people | `messages` reply graph |
| Fastest Fingers | quickest median reply time | `messages` |
| Conversation Starter | messages after 30+ minutes of silence that got the most replies | `messages` |
| Conversation Killer | last word before the longest silences | `messages` |
| The Perfectionist | most edits | `message_edits` |
| Kart Champion | most wins, plus fastest lap per track | `kart_results` |
| Rival of the Year | most PvP games against one person (bots excluded) | `pvp_results` |
| Most Dead | most Skyrim deaths | `skyrim_profiles.json` |
| The Collector | most county balls | `county_instances` |
| Badge Hunter | most badges earned in 2026 | `user_badges` |
| Paper Round | most newspapers delivered | `paperboy_runs` |
| Big Spender | most spent in the shop | `shop_purchases` |
| Rags to Riches | biggest climb from lowest to highest balance in the year | `user_transactions.balance_after` |
| Most Roasted / Most Shut | | `roast_targets`, `shut_counts` |

## More ideas

**Words**

- Laugh index: "lol" is used a quarter less per message than in 2025. What replaced it?
- Words of the month: the words that spiked each month, and slang that was born in 2026.
- Longest message of the year, scrolled past at speed as a joke.
- Swear jar: server total and swear of the year.
- Languages: what got translated most (`translation_log`).

**Time**

- The first message of 2026 (one second after midnight) and the last one.
- The quietest moment of the year: the longest silence in general.
- The peak minute.
- Busiest weekday (Tuesday, so far).

**People**

- Super reactions spent (`reactions.burst`).
- The year's polls and their winners (49 polls; final tallies can be read off the messages).
- Six degrees: the average number of hops between any two regulars in the reply graph.

**Server**

- Member count over the year, joins and leaves by month, boosts gained.
- New emoji added in 2026 and which of them caught on.
- HMS Victory's own year: messages sent, commands answered, the reserve it holds.
- A light mod slide: timeouts and bans since August.

**Money and games**

- Money supply over the year, and how much of it the top 10 hold.
- Peak balance of the year, and the biggest single-day swing.
- The casino's worst night, and the only games the house lost on.
- Lottery winners, bond investors, shutcoins.
- Daily games totals: height climbed, papers delivered, Spitfire high scores.
- Skyrim: dragons slain and deaths across the whole server.

**Format**

- A 15-second teaser a few days before.
- A "moment of the year" poll in December (poll votes are recorded now) feeding the last
  chapter.
- Hall of Fame nominations from members.
- Personal Wrapped: a tap-through version inside the /games activity, ending on a shareable
  card, built from the same slides and `queries.user_wrapped`.
- "Guess who" reveals: the stat first, a beat, then the avatar.
- A 2027 predictions slide.

## Building it

Everything lives in `scripts/wrapped/`. Four steps, all run from a Mac, none of them heavy on the VM:

```bash
HMS_VM=ubuntu@<host> HMS_VM_KEY=<key.pem> scripts/wrapped/pull.sh <data dir>
python3 scripts/wrapped/extract.py --data <data dir> --out <data dir>/wrapped.json --since 2026-01-01 --until 2027-01-01
python3 scripts/wrapped/build.py <data dir>/wrapped.json <out dir>/index.html
PLAYWRIGHT_CORE=<node_modules>/playwright-core/index.mjs node scripts/wrapped/render.mjs <url of index.html> wrapped.mp4
```

1. **Pull.** `pull.sh` streams last night's `chronicle.db.snapshot` (already a consistent copy),
   takes a SQLite backup of `database.db`, copies the JSON state files, and runs
   `fetch_discord.py` on the box to read names, avatars, channels and emoji through the bot.
   Keep the data dir out of the repo; it is every message the server has sent.
2. **Extract.** `extract.py` turns it all into one `wrapped.json` (about 150 KB) in under a
   minute. `--until` is exclusive and defaults to today. It writes `unresolved.txt` listing
   anyone it shows who has left the server; run `pull.sh <data dir> $(cat unresolved.txt)` and
   extract again to name them. Calendar captions for the busiest days are in `MOMENTS` at the
   top: check each new spike against what people were saying before trusting a caption.
3. **Build.** `build.py` bundles the player (`scripts/wrapped/player/`) and the JSON into one
   self-contained page. Opened in a browser it plays; `#scene=skyline` or `#t=42` jumps.
4. **Render.** `render.mjs` opens the page in headless Chrome with the GPU, steps every frame
   through `window.WRAP.seek`, and encodes 1080x1920 at 25fps. `--stills <dir> skyline+3 ...`
   renders single frames for checking a scene without a full render.

How the player works: every frame is a pure function of time. Scenes build their DOM once, and
`data-a` attributes, number rolls, pixel-reveal avatars, card flips and the three.js world are
all computed from the scene clock, so any frame can be rendered on its own and the video is
frame-exact. Scenes are listed in order in `scenes-a.js` and `scenes-b.js`; durations are on
each scene, and a scene whose data is missing (no casino, no Big Brother) drops itself.

Rules the extract already follows:

- Bots are filtered from every chat stat. HMS Victory otherwise tops one-sided replies and
  every PvP rivalry, because most PvP is Connect 4 against it.
- Days and hours are UK time (`local_day`, `local_hour`), never UTC.
- The longest message is shown by its size only, never its text.
- Words of the month leave out the names of anyone with 300+ messages, since names spike too.

## Timeline

- **October:** the gap fixes above.
- **November:** extract and stats against January to October; pick what's actually interesting;
  first scenes.
- **December:** script, music, polish, a full dry-run render around the 28th.
- **1 January:** final extract through 31 December, render, post.

## Open decisions

- Narrated (like the Big Brother replay) or music only, like last year.
- Whether stats that could embarrass someone (biggest casino loss, most deletes) name them or
  stay anonymous.
- Whether Personal Wrapped ships alongside the video or after it.
