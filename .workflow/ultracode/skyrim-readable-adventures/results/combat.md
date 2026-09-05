# Combat packet — DONE

Engine ownership is released to the parent as of 2026-09-05T17:15:05Z. No more edits by this packet are pending.

## Changes
- Added deterministic encounter intentions: charge/Guard, spell/Blade interruption, exposed flank/Bow extra damage, troll heal/Fire suppression. Dragons retain airborne/grounded/reflight mechanics, with charge counters while grounded. Room combat state survives restart; rendering does not mutate or roll it.
- Guard can be used once per foe. It safely answers a charge; otherwise a 50% strike risk buys +10% attack and no retaliation on the next landed follow-up. No Guard practice or repeat farming. `guard_hint` explicitly states the cost.
- Physical hits now train before a kill; missed attacks, failed stealth and failed persuasion give at most one attempt point per skill/room. A success tops up that skill's frozen normal learning allowance; the whole room caps at six skill points. Reloading/healing/Guard/Slip cannot multiply practice. Shouts no longer award arbitrary weapon practice or weapon-specific kill task credit.
- Added three fork stories (captive, brazier, runes): a choice changes the later guardian (one blocked blow, trap damage, an opening, or a richer theft that adds enemy HP). Vault choices add an elite and chest from a persisted seed, so identical shared maps do not drift with global RNG. Old plain fork rooms remain compatible.
- Fixed ambush crit snapshot and best-style selection by expected damage. Two-word non-dragon Shout now routes through ordinary kill/reward/collection resolution. Multi-damage hits/shouts correctly trigger reflight when they cross a threshold.
- Added `Delve.summary` baseline/terminal outcome and bounded `Delve.history` (24 messages). Added lethal-range healing warning, including two-HP danger and respecting Namira. Terminal actions are idempotent against extra button dispatches.

## Public interfaces
- `E.combat_intent(delve)` -> `key`, `label`, `hint`, `counter`, `max_wound`, `guard_available`, `guard_hint`.
- `E.Delve.act_guard(profile)`.
- `E.story_choices(delve)` -> list of `(emoji, label, action)` or None; `E.story_text(delve)` -> str or None.
- `d.capture_summary(profile)` snapshots once. `d.summary.start` includes skills, level, stats, tasks, gold, ingredients, potions, souls, words and gear. Terminal keys include banked_gold/lost_gold, banked_ingredients/lost_ingredients, skill_gains/stats_gains/task_gains, end_level, xp, kills, potions_used and outcome. Stories are retained as choice/outcome dictionaries.
- `d.history` is persisted and safely defaults to old `log` for historical boards.

## Required parent integration
- Call `capture_summary` on the original starting profile for daily/tutorial/Cairn before launch effects and task changes. `Delve.start` does this for normal/Alduin; first-action fallback supports old boards, but cannot reconstruct gains before that first capture.
- Parent owns launch/abandon settlement: route through act_leave so terminal summaries and ingredient banking apply. No changes were made to abandon_active/start_delve/start_soulcairn or progression/hunt/retirement functions.
- UI must route Guard, show intent/guard risk, use story choice helpers, and use summary/history fields. Experience agent received the precise APIs.
- `tutorial_completed` is set only when a tutorial clears; parent constructs guided rooms and gates repeat entry.

## Verification
RED: seven new plain regression/feature tests failed as expected before implementation (ambush, best-style, Shout rewards, practice, missing Guard/stories/summary APIs). All state was in memory.

GREEN, 2026-09-05T17:15:05Z, cwd `/Users/ogme01/Documents/Projects/HMS-Victory`:

`PYTHONDONTWRITEBYTECODE=1 /Users/ogme01/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 scripts/test_skyrim_isolated.py test_skyrim.py test_skyrim_combat`

Exit 0: **105 plain checks passed**, zero failures (89 unchanged baseline + 16 new combat checks). No unittest cases are in these two modules; the runner's `NO TESTS RAN` line refers only to that empty unittest suite. Covers actual intent effects, bounded contribution/miss practice across reloads, Guard persistence, seeded fork branches, delayed story consequences, readable lethal risk, Shout reward consistency, crossed reflight thresholds, debrief banking/loss, terminal repeat safety, old defaults and bounded history.

`git diff --check -- lib/features/skyrim/engine.py`: exit 0.

## Limits
No bot start, network calls, Discord sends, production state writes, commits or deploys. Tactical balance is a design change validated by mechanic tests; no live engagement or retention claim is made. Mobile/desktop payload and preview verification belongs to the UI/parent packet. Parent integration must rerun the full relevant suite after connecting progression and launch changes.
