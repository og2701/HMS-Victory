# Skyrim in the activity: the API contract

The whole /skyrim game, playable in the HMS Games activity (ukplace-activities). The bot keeps running the
existing engine (`lib/features/skyrim/`) as the one source of truth, so every save carries over and Discord
and the activity share a character. `lib/activities/skyrim_web/` turns the engine into JSON; the activity draws
it, animated (`ukplace-activities/src/skyrim/`).

All routes sit under the activity API prefix (`/api` locally), signed in like every other game (`_player`,
`_gate(client, uid, "skyrim")`). Errors are `{"error": "message for the player"}` with 4xx (422 for a refusal
the engine gave, 409 for a stale turn, 404 when there's nothing there).

TypeScript mirror of every shape: `ukplace-activities/src/skyrim/types.ts`. Keep the two in step.

## Conventions

- **Text.** Engine lines carry Discord markdown. The API passes them through `common.clean()`: `**bold**` stays
  (the client renders it), `-# ` and `## ` prefixes and `<t:..:R>` stamps are rewritten as plain text, `<@id>`
  mentions become display names, custom emoji are dropped, unicode emoji stay.
- **Art.** Keys, not URLs. `art` is a scene from `data/skyrim/<key>.webp` (copied to the activity as
  `public/skyrim/scene/<key>.webp`). `cut` is a cut-out (`public/skyrim/cut/<key>.webp`: `enemy_<art>`,
  `hero_<stone>_<pose>`). `stage` is an empty backdrop (`public/skyrim/stage/<key>.webp`).
- **Saving.** Every mutating handler does what views.py did: `get_profile`, mutate, `save_profile` (or the
  sessions prepare/commit pair for delves). All of it runs on the bot's event loop: never in a thread.
- **Names.** On every `GET /skyrim` the profile's `name` is refreshed from the member's display name.
- **Delve ids.** A delve started in the activity gets a negative id (`-time.time_ns() // 1000`), so restart
  reattachment (views.py `reattach_skyrim_view`) and the Discord hub can tell it has no message. A delve started
  in Discord can be carried on in the activity (it's the same record, keyed by `profile["active_delve"]`).

## Routes

| Method | Path | Body | Answer |
|---|---|---|---|
| GET | `/skyrim` | | `SkHome` |
| POST | `/skyrim/create` | `{stone}` | `SkHome` |
| GET | `/skyrim/offers` | | `SkOffers` |
| POST | `/skyrim/offers/{action}` | `elixirs {keys}`, `pacts {keys}`, `abandon {}` | `SkOffers` |
| POST | `/skyrim/launch` | `{loc, kind}` (`normal`, `daily`, `alduin`, `soulcairn`, `tutorial`) | `SkTurn` |
| GET | `/skyrim/delve` | | `SkTurn` (404 if none live) |
| POST | `/skyrim/act` | `{action, rev}` | `SkTurn` |
| GET | `/skyrim/panel/{key}` | | `SkPanel` |
| POST | `/skyrim/panel/{key}/{action}` | `{value?, values?}` | `SkPanelResult` |
| GET | `/skyrim/bout/{arena}` | | `SkBout` (`arena`: `pit` or `duel`) |
| POST | `/skyrim/bout/{arena}/{action}` | `{...}` | `SkBout` |

`action` for `/skyrim/act` is exactly the sessions token: `atk:blade`, `atk:marksman`, `atk:destruction`, `slp`,
`snk`, `per`, `guard`, `sht:1|2|3`, `pot`, `lve`, `evt:<choice>`. `rev` is the `SkDelve.rev` the player saw;
a mismatch answers 409 with the current turn in `{"error", "turn": SkTurn}`.

## Shapes

### SkHero (on most answers, the character at a glance)

```
{ name, stone, stoneName, archetype, level, xp, xpInto, xpNeed, septims, potions, potionCap, hearts (max),
  weapon: {name, tier, temper}, armour: {name, tier, temper, style}, souls, words, voice (charges),
  perkPoints, streak, delvesLeft, delveCap, nextDelveAt (unix s or 0), companion: {key, name, art} | null,
  faction: {key, name, rank} | null, legacy: {rank} }
```

### SkHome

```
{ hero: SkHero | null,                 // null: no character yet, show the class pick
  classPick: {intro: string[], stones: [{key, name, emoji, blurb, perks: string[]}]} | null,
  weather: {key, name, line}, dragonOfWeek: string,
  activeDelve: {id, location, room, rooms} | null,
  town: { adventure, character, shop, notice, pit, factions, holdings, hall, rankings },   // see below
  goal: {text, action} | null,          // action: a panel key, "adventure", or "delve"
  tutorial: bool }
```

Each `town` entry is `{label, glow: bool, badge?: string, locked?: string}`: what the building in the town hub
says, whether it's lit up (something to do there, as the green buttons were), a short badge ("2 perks",
"Claim!") and why it's shut if it is (the Pit before level 5).

### SkOffers (the world map)

```
{ hero: SkHero, weather, resting: bool, restUntil (unix), delvesLeft, delveCap,
  activeDelve: {...} | null, canAbandon: bool,
  tutorial: bool,
  locations: [{ key, name, band, blurb, minLevel, drops, route, routeTomorrow, stirred: {rank, name} | null,
                kind: "normal", locked: string | null }],
  daily: {key, name, mood, available, done} | null,
  alduin: {available, reason} | null, soulcairn: {available, best} | null,
  legends: [{key, name}],
  elixirs: {stock: [{key, name, emoji, count, blurb}], chosen: [keys]} | null,
  pacts: {open: bool, options: [{key, name, blurb}], sworn: [keys]} | null }
```

### SkDelve and SkTurn

```
SkDelve = { id, rev, kind, state,                  // state: playing|cleared|left|fled|dead|launched|abandoned
  location: {key, name, band}, depth, room: {idx, total | null, kind: "enemy"|"event", key, name, boss,
    art, cut | null, affix: {name, blurb} | null, bounty: bool, story: string | null (the story room's variant key, e.g. "runes"), text: string[] },
  enemy: { key, name, tier, hp, maxHp, intent: {key, label, hint, counter, guardAvailable, maxWound,
           guardHint}, airborne: bool, grounded: bool } | null,
  you: { hearts, maxHearts, satchel, potions, shoutCharges, venom: bool, blessed: bool, engaged: bool,
         ambush: bool, spotted: bool, buffs: string[], pacts: string[] },
  actions: [{ id, kind, label, emoji, pct | null, hint, disabled, style }],
            // kind: attack|sneak|slip|persuade|guard|shout|potion|leave|event
  log: string[],                                   // the newest lines, oldest first
  result: { line, summary: string[] } | null,      // when the run is over
  xpGained, kills }

SkTurn = { delve: SkDelve, cues: SkCue[], hero: SkHero, toasts: string[] }
```

### SkCue (what to animate, in order)

Worked out by comparing the delve and profile before and after the action, plus the log lines.

```
{ t: "hit", dmg, crit, style }        // the enemy took dmg (style: blade|marksman|destruction|shout)
{ t: "miss", style }
{ t: "kill", crit, boss }
{ t: "hurt", hearts, crushing }        // you lost hearts
{ t: "soak" }                          // armour, the fan or a pet took it
{ t: "heal", hearts }
{ t: "shout", cost, word }             // FUS / FUS RO / FUS RO DAH
{ t: "grounded" } { t: "airborne" }    // dragons
{ t: "sneak", ok } { t: "persuade", ok } { t: "slip" } { t: "guard" }
{ t: "loot", septims, items: [string] }
{ t: "xp", amount } { t: "level", level } { t: "soul" } { t: "word", word } { t: "wonder", key, name }
{ t: "advance", room }                 // walked into the next room
{ t: "event", key, choice }
{ t: "end", state }                    // cleared|left|fled|dead|launched
```

### SkPanel (every other screen)

```
{ key, title, art | null, back | null,              // back: the panel key (or "town") the back button goes to
  blurb: string[],
  stats: [{label, value, icon}],
  sections: [{title, lines: string[]}],
  actions: [{ id, label, emoji, style, disabled, hint, nav | null, confirm | null }],
           // style: primary|success|danger|secondary; nav: open that panel (or "adventure", "delve",
           // "bout:pit", "bout:duel") instead of posting
  selects: [{ id, placeholder, min, max, options: [{value, label, blurb, emoji, chosen}] }] }

SkPanelResult = { panel: SkPanel, toast: string | null, cues: SkCue[], nav: string | null, hero: SkHero, march?: SkMarch }

// the Notice Board's march on the week's hunt, staged as a fight before the board redraws
SkMarch = { boss: {key, name, cut, art, stage, dragon: bool}, role: {key, label}, hearts: number,
            pool: {hp, max, wave},                       // the shared pool before the march
            beats: [{ line: string, cue: SkMarchCue | null }],
            dealt: number, slain: bool, after: {hp, max, wave, next: string | null, nextKey: string | null} }
SkMarchCue = {t: "hit", dmg, crit, style} | {t: "miss", style} | {t: "hurt", hearts, crushing, left}
           | {t: "beat"} | {t: "down"} | {t: "kill", crit, boss} | {t: "rise"}
```

Posting a select sends `{values: [..]}` (or `{value}` when `max` is 1).

### SkBout (the Pit and ghost duels)

```
{ arena: "pit"|"duel", state: playing|won|lost|draw|none, round,
  me: {name, hp, maxHp, cut}, foe: {name, title, art, hp, maxHp, quirk | null},
  actions: [{id, label, emoji, hint, disabled}], next: [{id, label}],   // next: fight on / bank it, after a win
  lines: string[], cues: SkCue[], hero: SkHero }
```

## Panel keys

Each one is a port of a views.py screen: same rules, same engine calls, same wording, as data.

| key | views.py | actions |
|---|---|---|
| `character` | `_hub_character`, `_sheet_text` | nav to perks, masteries, collection, records, companion, hall |
| `perks` | `_hub_perks` | `take` (select), `meditate` |
| `masteries` | `_hub_masteries` | `doctrine` (select `skill:choice`), `legendary` (select) |
| `collection` | `_hub_collection` | |
| `records` | `_hub_records` | |
| `companion` | `_hub_companion` | `choose` (select) |
| `shop` | `_hub_shop`, `_shop_text` | `potion`, `weapon`, `armour`, `style`, nav property, grindstone, alchemy, rumours |
| `property` | `_hub_property` | `buy` (value: home key) |
| `rumours` | `_hub_rumours` | `buy` (select) |
| `alchemy` | `_hub_alchemy` | `brew` (select) |
| `grindstone` | `_hub_grindstone` | `weapon`, `armour` |
| `pacts` | `_hub_pacts` | `swear` (select, multi) |
| `notice` | `_hub_notice`, `_notice_text` | nav daily (launch), `daily_board` nav, `claim`, `march` (value: role), `spoils` |
| `daily_board` | `_daily_results_text` | |
| `pit` | `_hub_pit` | `step_in` (nav bout:pit), `duel` (select rival → nav bout:duel) |
| `factions` | `_hub_factions`, `_factions_text` | `claim`, `promote`, `join` (select, then confirm) |
| `holdings` | `_hub_holdings` | `deed`, `collect`, `haul` (value: slot), `build`, `expedition`, `banner`, `shrine` (selects) |
| `hall` | `_hub_hall`, `_hall_text` | `boon` (select), `inherit` (select), `stone` (select), `retire` (confirm) |
| `rankings` | `_hub_rankings`, `_rank_metric` | `board` (select of the eight boards) |
| `help` | `_hub_help`, `HELP_PAGES` | `page` (select) |
