# Readable Skyrim adventures

## Objective and exclusions
Implement all agreed Skyrim improvements with compact mobile and desktop Discord UI

Implement every improvement proposed in the conversation: tactical enemy intentions, guided onboarding and useful goals, connected dungeon choices, cooperative hunts, contribution-based practice and richer faction/rebirth progression, forgiving reward retention and stamina scheduling, and the identified correctness fixes. Keep Discord play clear on phones and desktop. Work locally; no deployment, Discord sends, commits or live player-data changes are requested.

## Success criteria
1. Delves expose compact enemy intentions with real counters; actions give short outcomes, detailed explanations are available on demand, and lethal damage is telegraphed.
2. New players receive a short guided first adventure. Hub displays one actionable next goal. Finished runs show banked/lost gold and ingredients, skill progress and relevant unlocks/tasks with a contextual action.
3. Existing forks support reusable connected stories whose choices change later fights, with persisted outcomes and no added wall of text.
4. The weekly hunt supports attack, expose and protect, with bounded asynchronous support and visible attribution.
5. Successful contributions and failed practice give bounded skill growth; factions have rank-specific promotion missions; rebirth preserves a chosen learned ability.
6. Earned weekly task and hunt rewards survive rollover exactly once. Stamina remains +1 per 4h with a more forgiving bounded storage allowance.
7. Replacing/abandoning a run obeys the same exit rules, ingredients and pact rewards as leaving. Launch failure consumes no attempt, supplies or existing run. Missing Vigor healing and ambush crit mismatch are fixed; shout rewards and auto-style selection are consistent.
8. Across hub, combat, shop, progression and results: short default summaries, paged/detail views where needed, short button labels, <=3 controls per action row where feasible, no wide comparison tables or excessive heart strings. Verify representative 360px and desktop layouts using actual Discord component payloads plus an explicitly approximate local visual preview.

## Workspace and baseline
Repository: /Users/ogme01/Documents/Projects/HMS-Victory. Working tree was clean at start. Baseline is recorded in state.json. Existing engine is JSON-persisted, single event loop; public delves resume by message ID. Existing 89 Skyrim checks passed during analysis with all state in temporary files.

## Constraints and authority boundary
Never use grep/find. Preserve old profiles and persistent board state. Do not start the bot or mutate actual data/json files or databases. No extra UI menu proliferation; Inspect and existing panels carry detail. New randomness must not drift a shared daily layout between players.

## Risk and assumptions
Gameplay and persisted-state changes are medium risk, with important failure paths. Unit and integration tests must isolate every file destination. Browsers can only show a local approximation; do not claim real Discord client testing without it.

## Design decisions
- Keep the existing engine and Discord components. Extract focused combat/progression/presentation helpers where useful rather than a framework rewrite.
- Short primary screen, optional Inspect, contextual outcome and next action. Images remain scenery, never encode critical controls or text.
- Combat agent owns engine.py until handoff; progression helpers are implemented independently and then integrated by parent.
- UI agent owns views.py. Launch/settlement transaction design uses new parent-owned sessions.py; UI calls that API.
- Raise stored delves from 5 to 12 (two days of fixed refill slots), preserving refill rate and daily content limits.

## Packets, dependencies, and ownership
- combat: engine.py, combat.py, tests/test_skyrim_combat.py; intentions, bounded practice, connected fork stories, ambush/shout fixes, resumable encounter state. Parent integrates other engine changes only after handoff.
- progression: progression.py, tests/test_skyrim_progression.py; isolated helper APIs for reward carryover, cooperative hunt roles, faction missions, inherited ability, next-goal data. Provide engine integration instructions; no edits to engine.py or views.py.
- presentation: views.py, presentation.py, tests/test_skyrim_readability.py; complete compact player UI, first-adventure guidance, details, navigation, controls and summaries. Uses documented helper interfaces below.
- parent: sessions.py, config.py, data.py, test runner and integrated tests, engine integration after combat handoff, visual preview and independent review.

## Integration interfaces
- combat intent: E.combat_intent(delve) -> dict with key/label/hint; E.Delve.act_guard(profile); E.story_choices(delve) -> list of (emoji,label,action) or None; E.story_text(delve) -> str or None. Rooms contain new combat/story fields and therefore survive current room serialization.
- debrief: delve.summary stores starting skill/level/task/inventory data and terminal bank/loss data; backward-compatible empty defaults. UI may calculate from snapshot/current profile.
- progression module P: next_goal(profile) -> {text,action}; promotion(profile) -> dict or None; inheritance_options(profile) -> list of {skill,choice,label}; inherit(profile,skill,choice) -> error-or-None. Parent connects underlying engine entry points.
- hunt: E.wb_march(profile, role='attack'); role in attack/expose/protect. P contains role definitions and bounded support helpers; parent integrates loops.
- launch: sessions.launch_lock(user_id) async context; sessions.prepare(profile,channel_id,loc_key,kind) -> pending staged object; pending.profile, pending.delve, pending.commit(message_id). Preparation must not persist changes, failed post discards it, commit settles the old run and saves new profile/board. Return errors as ValueError. UI holds lock through send/commit.

## Integration order
Parallel engine combat, standalone progression helpers and UI. Parent builds isolated runner and launch transaction. Accept helper handoffs, connect all engine hooks, reconcile UI APIs, add integration/failure/recovery tests, review, verify full Skyrim suite and mobile/desktop sample payloads.

## Verification plan
Use bundled Python with installed discord added to sys.path; run all test_skyrim*.py functions using a reusable standalone runner or pytest if available. Every game path redirects to temporary storage. Exercise fresh/profile-migration/resumed boards, combat counter choices, skill bounds, fork consequences, hunt role attribution, rollover payouts twice, inheritance/faction rewards, concurrent/failed launches, abandonment restrictions and healing/crit regressions. Render representative real component payloads in a labelled local preview at 360 and desktop widths and inspect for overflow, control labels, text volume and hierarchy. Independent read-only review covers full requirements and failure handling after integration.

## Decisions and recovery notes
Initial execution began 2026-09-05. The full objective remains active until all eight criteria have authoritative current evidence. No gameplay deployment is included.
