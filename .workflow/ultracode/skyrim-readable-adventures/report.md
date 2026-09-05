# Readable Skyrim adventures

Implemented locally in `/Users/ogme01/Documents/Projects/HMS-Victory`. No deployment, commit, push, bot connection or live player-data changes.

## Outcome

- Short default cards: numeric health, visible enemy intent and counter, one recent outcome, clear actions, and Inspect for history/mechanics. Character name and status use separate lines so long names do not strand a number on a phone.
- A free four-room first adventure, a single actionable next goal, and run debriefs showing banked/lost gold and ingredients, skill/task progress and unlocks.
- Connected scout, brazier and rune choices alter the final fight. Guard is a bounded tactical option. Useful hits and initial misses train skills without rewarding repeated failures indefinitely.
- Hunts offer Attack, Expose and Protect. Support costs 10 hit percentage points, benefits the next different ally, expires after 24 hours, and names the helper. Expose can raise the normal 86% hit cap to 98%.
- Faction ranks now require distinct promotion missions as well as favour; existing ranks are preserved. Rebirth lets players choose one learned doctrine to retain.
- Completed weekly task rewards settle on rollover; hunt rewards carry forward as claimable receipts and pay once. Stored delves increased from 5 to 12 while refill remains one every four hours.
- Adventure replacement obeys real exit/pact/flee/material rules. Failed board sends preserve the existing run, attempts and supplies. A durable launch journal repairs an interrupted second-file write at restart.
- Vigor healing, displayed ambush critical chance, automatic combat style selection and Shout defeat rewards are consistent. Two-word Shout now deals bounded damage, preventing instant full-health legend kills.

## Fresh verification evidence

Final isolated command (exit 0):

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/ogme01/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 scripts/test_skyrim_isolated.py
```

106 historical/plain checks and 52 unittest checks passed: **158 total, zero failures**. Exact output is `results/tests.txt`; expected mocked Discord send/commit failures are logged during passing tests. All profile, hunt, daily and persistent-view paths point to disposable state. Coverage includes legacy migration, duplicate claims, weekly resets, role support, stale launch races, failed sends, crash recovery, original daily dates, tactical counters, skill caps, story save/load, inheritance, promotions, real component limits and private navigation.

A further 300 seeded guided-adventure smoke runs reloaded the board after every action and terminated without errors in at most 12 actions (199 clears, 101 deaths). This checks state/flow behavior, not player balance. Result: `results/tutorial-smoke.json`.

17 actual component samples were exported with `scripts/preview_skyrim.py` and reviewed in the browser at 360px and 780px card widths. Ten core screens also passed with a 340px card inside a 360px phone viewport. No overflowing button rows or clipped button labels. Mobile combat, choice, hub and debrief screenshots were inspected; the last changed combat card was rechecked. Preview: `preview/index.html`. Details: `results/visual-review.json`.

Fresh read-only integration review completed; all concrete findings resolved. `git diff --check` passed, and changed Python files parse successfully.

## Remaining risks

This is a local implementation. The visual preview approximates Discord; actual Discord clients and a deployed bot have not been exercised. Live playtesting is still needed to tune difficulty and rewards from player behavior. No publishing or deployment is part of this result.

## Requirement coverage

1. Tactical intent, Guard, bounded practice and lethal-risk warning: engine/combat tests plus real UI payload checks.
2. Guided first adventure, next goal and debrief: integration/readability tests and 300 save/reload smoke runs.
3. Connected choices: scout, brazier and rune effects persist into later fights; combat tests cover effects and reload.
4. Cooperative hunt: all three roles, support bounds, attribution, veteran cap and daily allowance covered by progression/integration tests.
5. Faction missions and inherited ability: promotion eligibility/rewards, existing-rank migration and rebirth integration checks pass.
6. Reward retention and stamina: rollover, duplicate/retried claim and multi-wave receipt checks pass; stored charges are capped at 12 with unchanged refill rate.
7. Correctness fixes: replacement exits, failed sends, journal recovery, stale state, daily date, Vigor, ambush, Shout and auto-style regressions pass.
8. Readability: actual component limits and navigation tested; 17 primary preview screens inspected at mobile and desktop card widths.

## Changed paths and decisions

`lib/features/skyrim/engine.py` integrates the game rules; focused new helpers are `combat.py`, `progression.py`, `sessions.py` and `presentation.py`. `views.py` applies compact summaries and Inspect navigation across the existing game. `config.py` changes the stamina cap, and `lib/bot/event_handlers.py` recovers committed launches before registering saved boards.

The existing JSON stores and Discord component architecture are retained. A profile-side launch journal makes two-file completion recoverable; immutable hunt receipts keep retry evidence with the balance update. Focused tests and `scripts/test_skyrim_isolated.py` make validation independent of a running bot. `scripts/preview_skyrim.py` exports the approximate local visual review.

## Skipped checks

No Discord account connection, actual mobile/desktop Discord-client test, bot startup against live data, deployment or production playtest was performed. These actions were outside the authorized local implementation scope. The browser preview is explicitly approximate and cannot prove Discord client rendering.
