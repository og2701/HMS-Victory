# Progression handoff

Status: DONE (helpers and isolated tests). Engine/UI integration belongs to parent and presentation agent.

Files: `lib/features/skyrim/progression.py`, `tests/test_skyrim_progression.py`.

Verification: `PYTHONDONTWRITEBYTECODE=1 /Users/ogme01/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 scripts-local/test_skyrim_isolated.py test_skyrim_progression` passed 15 unittest checks. All engine file paths redirected into temporary directories. No real game data reads/writes, bot/network, commits or deployments.

## Required engine hooks

Import `from lib.features.skyrim import progression as P` near the data import. P imports engine lazily, so this does not create an eager cycle.

### Earned weekly task rewards

In `task_state`, immediately BEFORE `ts.clear()` in the week-change branch:

```python
P.settle_task_rollover(profile, wk)
```

This pays completed unclaimed tasks using the previous week's deterministic task draw, including an earned sweep bonus; partial tasks earn nothing. It sets the old tracker claim markers and credits the same profile, so the ordinary single profile save makes the credit/receipt atomic. Retrying an unsaved profile yields the same resulting balance; retrying a saved one does not pay again. It leaves concise messages in `profile['reward_notices']` (last four only). UI should surface the newest notice when appropriate. Caller should save after rendering a task state that rolled over, as existing Notice UI does.

### Hunt mailbox and rollover

`world_boss` currently replaces old store state. Retain a reference/copy to the old store, and before saving replacement state call:

```python
P.preserve_hunt_mailbox(old_store, new_store)
```

Before returning a current-week store from `world_boss`, call `P.capture_hunt_rewards(store)` and `_wb_save(store)` if it returns true. This migrates old unclaimed shares and persists immutable receipts before any caller can deliver them. Legacy shares already marked claimed are not recaptured.

After a wave dies, AFTER computing all share payouts INCLUDING the extra killing-blow 400, but BEFORE `_wb_next_wave` changes the boss identity:

```python
P.capture_hunt_rewards(store)
```

Keep existing share calculations for compatibility. Capturing records only the delta since the previous capture. A newly-created share after a prior claim gets a new monotonically increasing per-player receipt number. Each receipt includes week and original boss, so UI should use generic `Hunt rewards` text rather than naming the new wave's boss as the defeated one.

Replace `wb_share_waiting` implementation with `P.hunt_rewards_waiting(profile, store or world_boss())`. This preserves expected `septims`, `xp`, `claimed=False` keys plus a `receipts` list.

Replace payout internals of `wb_claim` with:

```python
store = world_boss()
line = P.claim_hunt_rewards(profile, store)
if not line:
    return None
save_profile(profile)
_wb_save(store)   # advisory old share flag only; receipt remains in mailbox
# Existing wonder roll can happen here, with save_profile again after its result.
return line
```

Critical invariant: a balance credit and `profile['hunt_receipts']` IDs must be saved together. Do not mark/delete shared receipts to represent delivery. Shared `shares[uid]['claimed']` is now merely advisory for compatibility. Even if this flag saves before a profile write fails, immutable mailbox receipts still pay the unchanged profile on retry; if the profile saves but the shared flag does not, profile IDs prevent duplication. The helper itself does not write files. Caller can save profile before shared store for clarity.

Receipt growth is intentional and small per weekly wave per hunter. Do not prune IDs alone. A future compactor may remove matching receipt IDs from BOTH the durable mailbox and the profile only after introducing a durable compaction journal/high-water mark that survives either file save order. No unsafe retention limit is applied now.

### Cooperative hunt

Change `wb_march(profile)` signature to `wb_march(profile, role='attack')` and validate availability and role before any mutation. Only on an accepted actual march:

```python
support = P.consume_hunt_support(store, profile, role)
atk = _clamp(_wb_attack_pct(profile, boss) + support['attack_bonus'])
guard = min(SOAK_CAP, soak_pct(profile)) + support['guard_bonus']
fatk = max(5, boss['fight'] - guard)
lines.extend(support['lines'])
```

Attack has no modifier. Expose/protect trade -10 hit percentage points for a +12 hit/+12 guard effect on the next different hunter. The store holds at most one effect of each type, expires after 24 hours, cannot be consumed by its provider, and gives visible attribution. Preview/read calls must not call `consume_hunt_support`.

After resolving the march, before its normal store save:

```python
help_line = P.finish_hunt_support(store, profile, role)
if help_line:
    lines.append(help_line)
```

Effects carry to a newly spawned wave in the same week (same warband), but not to a fresh week. Both effect types can help one ally, each only once. `P.HUNT_ROLES` contains label/emoji/hint for UI.

### Faction promotion missions

In profile migration AFTER existing legacy-favour migration, call `P.ensure_promotions(profile)`. Also call before incrementing favour in `claim_faction` and before changing allegiance in `join_faction`, so an old rank is grandfathered before any new progress. New characters start with rank zero, existing earned ranks never drop.

In `task_event`, call `P.promotion_event(profile, kind, **ctx)` once. It uses the same event context filters and does not recursively emit tasks. Missions progress across week boundaries and only the current promotion counts. Goals vary by rank and faction; favour gates 2/4/6/8 remain.

Delegate `faction_rank(profile,key=None)` to `P.faction_rank(profile,key)`. For `claim_faction` stipend and return/log rank, use `P.faction_rank_index(profile)` rather than `favours // 2`. The mission claim now grants new ranks. The favour increment itself still belongs to the normal weekly activity.

UI calls `P.promotion(profile)` (None or `{faction,tier,rank,label,progress,goal,complete,eligible,claimable,reward,title,favour_needed}`). When claimable, `P.claim_promotion(profile)` returns None on success or a short error; save profile. Each rank grants two elixirs, and final rank grants a faction-specific title saved in `profile['titles']`. No extra panel required. Existing Faction panel should show one trial line and one claim control.

### Chosen inheritance

`P.inheritance_options(profile)` returns learned choices as `{skill,choice,label}`. `P.inherit(profile,skill,choice)` validates a currently learned doctrine and stores `profile['inheritance']={skill,choice}`, returning error or None. UI saves this choice before confirmation.

In `retire`, after readiness/boon/stone validation and BEFORE any reset:

```python
inherited = P.prepare_inheritance(profile)
```

After the existing doctrine reset:

```python
P.apply_inheritance(profile, inherited)
```

This clears all doctrine state and restores exactly the one validated choice, with its normal existing effect even at starter skill level. No mastery level/star is inherited. A missing, forged, stale or removed option carries nothing. It keeps `profile['inheritance']` for display and a future default, but a later selection can replace it. The existing doctrine slots allow the inherited choice plus a later Legendary-earned second choice as before.

Retirement already clears faction favour: also set `profile['promotions'] = {}` followed by `P.ensure_promotions(profile)` after clearing favours. Titles, hunt receipt IDs and reward mailbox notices are account state and MUST persist through retirement; otherwise old rewards could replay.

### One next goal

`P.next_goal(profile)` returns exactly `{text,action}`. Vocabulary: `tutorial`, `adventure`, `notice`, `perks`, `shop`, `factions`, `hall`. Tutorial has highest priority when `E.tutorial_available` exists and is true. It otherwise chooses a running adventure, useful perk, ready promotion/retirement/upgrade, current trial, faction joining, bounty/daily, level/Word/Alduin/Cairn goal. Text is short enough for one or two phone lines. Render one goal and route through existing panels, not an extra dashboard.

## Coverage and residual integration work

Tests verify old/current/malformed task weeks, partial/sweep payouts, save/restart retries; hunt migration/rollover, multiple wave increments, both file failure orderings, no duplicate legacy claims; support attribution, expiry, no self-benefit, bounded stacking and role cost; old-rank grandfathering, favour+trial gates, correct event filter and once-only elixir/title reward; forged/stale selection and exactly one inherited doctrine; tutorial-first short goals.

Parent still needs engine hook and full end-to-end tests. Presentation agent has interface contracts and owns faction/hall/goal/hunt UI. No engine or views files were edited by this agent.
