# Report: HMS Victory deep optimisation audit

## Outcome

HMS Victory has unusually solid foundations for a single-process Discord bot: core UKP transfers are transactional and failure-tested, database backups use SQLite's online snapshot API, restore validation is strict, scheduler registration is reconnect-safe, and anti-raid disable/restore fails closed. The best next work is therefore not a rewrite. It is to close three remaining integrity/authorization gaps, remove synchronous hot-path work from the event loop, and turn background operations into durable, observable workflows.

The five highest priorities are: re-authorize every role-changing callback; make wager/prediction/benefit state changes atomic and restart-idempotent; enforce `/pay` caps inside the transfer transaction; add durable period claims to monetary jobs; and move SQLite/file amplification off the Discord event loop. These protect money, permissions, and availability before broader UX work.

Scores are qualitative. Impact and confidence are `High/Medium/Low`; effort is `S/M/L/XL`; operational risk is the risk of implementing or rolling out the recommendation, not the risk of leaving the current system unchanged.

## Ranked backlog of fifteen improvements

| Rank | Recommendation and current evidence | Impact | Effort | Confidence | Operational risk | Class |
|---:|---|:---:|:---:|:---:|:---:|---|
| 1 | **Harden every role-changing surface with fresh authorization and explicit intent.** The bulk `Give Role` callback performs no clicker authorization before assigning a role to every target (`commands/moderation/role_management.py:48-79`). Announcement role buttons accept any staff-selected role without an explicit self-assign allowlist (`commands/moderation/announcement_command.py:94-125,176-183`). `/toggle-quarantine` bypasses anti-raid release history and uses state inversion (`lib/bot/setup_commands.py:253-259`; `lib/core/discord_helpers.py:49-65`). Bind bulk callbacks to the initiating actor, recheck current permissions and role hierarchy, allowlist self-assignable roles at creation and click time, and replace moderation toggles with Apply/Remove plus reason and audit ID. | High | M | High | Low | Quick win, then platform hardening |
| 2 | **Move wager, prediction, benefits, and similar financial claims into durable atomic state machines.** Wager escrow debits users sequentially and keeps the resolution claim only in memory (`commands/economy/wager.py:17-57,121-170`). Prediction stake and settlement cross SQLite and JSON boundaries (`lib/economy/prediction_system.py:72-91,883-921`). Benefits mark claims before payout and clear fines after debit (`lib/features/ukp_rewards.py:662-678,737-779`). Give every operation a durable unique ID; commit escrow/claim/status and money movements together; recover or safely resume incomplete terminal transitions. | High | XL | High | High | Architectural project |
| 3 | **Enforce the `/pay` daily cap inside the same transaction as the transfer.** The cap sum is read first and the balance transfer/audit commits later (`lib/bot/setup_commands.py:438-492`), so concurrent interactions can both pass the check. Extend the transfer primitive so cap calculation, conditional claim, debit, credit, and audit row are one locked transaction. | High | M | High | Low | Quick win |
| 4 | **Give every monetary/background job a durable, period-keyed execution ledger.** APScheduler state is process-local; daily rewards and weekly taxes/demurrage lack a durable run claim (`lib/bot/scheduled_tasks.py:514-568`). Add unique `(job, UK-period, subject)` claims, explicit `coalesce`, `misfire_grace_time`, `max_instances`, retry/timeout policy, and startup reconciliation. Catch-up must not ship until each job's money movement is idempotent. | High | L | High | High | Architectural project |
| 5 | **Put SQLite behind one nonblocking execution boundary.** A shared synchronous connection and `RLock` run directly inside async handlers, with a five-second busy timeout (`database.py:7-93`; `lib/features/xp_system.py:199-221`; `main.py:230-285`). Use a dedicated ordered DB worker/repository boundary that preserves existing transaction semantics and exposes queue/statement latency. A partial conversion that mixes nested sync and async transaction calls is unsafe. | High | L | High | Medium | Architectural project |
| 6 | **Remove per-message whole-blob and whole-file amplification.** Every normal message initializes summary data and performs two read/parse/write updates (`main.py:283-285`; `lib/features/summary.py:197-311`), plus a message-archive commit and XP/account checks (`lib/features/message_archive.py:33-43`; `lib/features/xp_system.py:199-221`). Replace summary JSON blobs with atomic SQL counters or a bounded flush buffer, use `INSERT OR IGNORE` instead of existence reads, cache durable XP cooldowns, and batch archive writes with shutdown flushing. | High | M/L | High | Medium | Staged architectural project |
| 7 | **Make recovery select the newest valid generation and add independent, provable backups.** Database and JSON restore stop at the newest filename match (`lib/bot/backup_manager.py:385-409,754-802`); backups live in one Discord channel (`lib/bot/backup_manager.py:814-876`). First add bounded newest-to-oldest validation fallback. Then add signed manifests, encrypted independent storage, retention policy, RPO/RTO, and automated isolated restore drills. Preserve all current strict schema, invariant, path, size, and atomic-promotion checks. | High | M then L | High for fallback; Medium for offsite assumptions | Low then Medium | Quick win plus architectural recovery program |
| 8 | **Build a retained operational outbox, structured telemetry, and staff health dashboard.** Economy log rows are deleted after Discord delivery and disappear when neither destination is available (`lib/bot/scheduled_tasks.py:765-811`); backup failures only log (`lib/bot/backup_manager.py:869-870`); logging is basic INFO/syslog (`main.py:30`; `deployment/hms-victory.service.template:16-18`). Retain sent timestamps instead of deleting forensic rows, add retry/dead-letter state, correlation IDs, queue age/depth, event-loop lag, backup age, DB invariant/quick-check results, and a read-only `/admin health` view. | High | L | High | Low | Architectural project |
| 9 | **Offload, serialize, and stagger backup work immediately.** The five-minute async backup calls `snapshot_to_file` synchronously while holding the global DB lock; only compression is threaded (`lib/bot/scheduled_tasks.py:547-549`; `lib/bot/backup_manager.py:843-868`; `database.py:95-107`). Move snapshot work to the DB worker/thread, guard against overlap, stagger DB/JSON schedules, and record duration/size/outcome. | High | S/M | High | Low | Quick win |
| 10 | **Add proactive, human-controlled anti-raid detection.** Join velocity is already calculated but only shown when staff opens/refreshed the control centre (`commands/moderation/anti_raid.py:255-261,621-653,912-950`). Add configurable multi-window/young-account thresholds, deduplicated staff alerts, acknowledgement/escalation, and simulation tests. Start alert-only; preserve the explicit no-automatic-ban design (`commands/moderation/anti_raid.py:951-954`) and do not auto-enable lockdown until production baselines and a separate policy decision exist. | High | M/L | High | Medium | Architectural project |
| 11 | **Make quarantine and moderation control complete at incident scale.** The panel shows ten members and the selector exposes only the first 25 (`commands/moderation/anti_raid.py:656-705`); release acts only on those selected (`commands/moderation/anti_raid.py:817-872`). Add paging/search/filtering, stable cross-page selection, explicit bulk confirmation, bounded batches, reasons, and per-member results. Route all quarantine changes through this service so history, notification, and audit behavior cannot diverge. | High | M | High | Medium | Quick win |
| 12 | **Make the inbox the canonical outcome record, then improve discovery.** Producers cover badges, shop, bonds, and anti-raid, but automod and wager outcomes remain DM-only and demurrage is silent (`lib/bot/event_handlers.py:392-410`; `lib/economy/bonds.py:245-269`; `main.py:253-278`; `commands/economy/wager.py:72-88`; `lib/bot/scheduled_tasks.py:755-762`). Introduce a deduplicated notification service, severity/category policy, DM as optional delivery, unread/category filters, per-item controls, unobtrusive unread discovery, and retention that never silently drops unread critical notices. | High | M/L | High | Low | Architectural project with quick UX increments |
| 13 | **Create a command platform: native permissions, groups, error handling, and abuse controls.** Roughly 67 flat slash commands share a custom wrapper that logs but has no native guild/default permissions, global error response, cooldown, or concurrency policy (`lib/bot/setup_commands.py:83-110,134-762`). Group staff/economy/prediction commands, keep high-frequency user commands easy to reach, apply `guild_only`/default permissions, centralize acknowledgement/error/correlation behavior, and add token buckets/semaphores for AI/render work. Migrate hidden `roleadd`, `shopadmin`, `titleadd`, and raw `hmsql` message triggers to private audited commands (`lib/bot/event_handlers.py:1248-1328`). | High | L | High | Medium | Architectural project |
| 14 | **Adopt versioned migrations, storage constraints, and query-aligned indexes.** `init_db()` mixes 700 lines of boot-time DDL, exception-driven migration, repair, and seed work (`database.py:250-956`). Foreign keys are not enabled and UKP/transfer tables lack database-level value checks (`database.py:18-25,260-263,509-548`). Introduce ordered transactional migrations, representative old-schema fixtures, preflight data audits, `foreign_keys=ON`, appropriate integer/non-negative/positive checks, and composite actor/timestamp plus `(status, scheduled_ts)` indexes validated by query plans. | High | L | High | High | Architectural project |
| 15 | **Split startup and feature ownership without a rewrite.** Startup uses wildcard imports and global client/tree construction (`main.py:17-20,517-520`); `event_handlers.py` is 2,060 lines, `prediction_system.py` 2,007, and `shop_ui.py` 1,401. Ready initialization sleeps for every command and performs stage refresh plus two backups inline (`lib/bot/event_handlers.py:900-928`). Define critical-ready versus tracked post-ready work, remove artificial sleeps, sync commands only when the manifest changes, then introduce an application factory/service container and feature-level command/event registration incrementally. | Medium/High | XL | High | Medium | Architectural project; startup phase has quick wins |

## Requirement coverage

- Startup lifecycle: ranks 9 and 15.
- Scheduler/background jobs: ranks 4, 8, 9, and 10.
- Database access, transactions, concurrency, caching, and unnecessary work: ranks 2-6, 9, and 14.
- Module boundaries and maintainability: ranks 13-15.
- UKP integrity, idempotency, recovery, permissions, rate limits, failure handling, and observability: ranks 1-5, 7-10, 13, and 14.
- Commands, inbox, moderation/anti-raid, admin ergonomics, automation, and missing capabilities: ranks 1, 8, and 10-13.

## Quick wins

1. Rank 1: close the proven bulk-role callback authorization gap and validate self-assign roles at both creation and click time.
2. Rank 3: put `/pay` cap enforcement in the transfer transaction with a concurrent regression test.
3. Rank 9: offload and serialize database snapshots; stagger DB/JSON schedules.
4. Rank 11: page/search the quarantine list and require explicit confirmed releases.
5. Rank 7, phase one: try older candidates when the newest backup fails strict validation.
6. Rank 15, phase one: remove the 100 ms per-command ready delay and move backups into tracked post-ready work.
7. Supporting hygiene: pin runtime dependencies, add a test extra/lock, include every test module in CI, fix README backup wording, and correct the setup script's service-template path (`requirements.txt:1-11`; `.github/workflows/tests.yml:20-35`; `README.md:53-63`; `setup_instance.sh:54-67`).

## Larger architectural projects

- Durable economy state machines and claims: ranks 2, 4, and the notification event keys in 12.
- Event-loop and data-path redesign: ranks 5, 6, 8, and 9.
- Recovery and operational assurance: ranks 7 and 8.
- Operator/product platform: ranks 10, 12, and 13.
- Migration and code ownership foundations: ranks 14 and 15.

## Dependencies and changes not to attempt independently

- **Do not add scheduler catch-up before durable claims.** Rank 2's atomic operation IDs and rank 4's period ledger must exist before missed jobs are replayed; otherwise catch-up can double-pay or double-charge.
- **Do not partially async-convert nested DB calls.** Design rank 5's ordered worker and transaction API first, then migrate rank 6/9 callers while rerunning every economy failure-injection test.
- **Do not enforce new schema constraints blindly.** Rank 14 requires a read-only production data audit, old-schema fixtures, backup, and rollback plan before table rebuilds.
- **Do not automate raid enforcement from guessed thresholds.** Ship rank 10 alert-only, collect false-positive evidence, preserve manual decisions and no automatic bans, then seek a separate policy decision.
- **Do not delete JSON compatibility outputs until recovery changes.** Ranks 2, 6, and 14 must migrate restore/export consumers before JSON becomes derived-only.
- **Do not make inbox persistence part of the business transaction.** Rank 12 should consume a durable event/outbox after the money/moderation action commits; a notification failure must not undo a completed action.
- **Centralize moderation before adding more controls.** Rank 1's explicit action/audit service should be reused by rank 11 and command reorganization in rank 13.

## Top five recommended next actions and acceptance criteria

1. **Close role authorization and intent gaps.** Acceptance: unauthorized bulk-role clicks change zero members; allowed self-assign roles are explicitly configured and checked twice; stale permissions/hierarchy fail closed; quarantine Apply/Remove is idempotent and records actor, target, reason, result, and correlation ID; focused tests cover each case.
2. **Make `/pay` cap enforcement transactional.** Acceptance: two concurrent transfers that together exceed the cap produce exactly one commit; balance, both ledger legs, and `pay_transfers` remain atomic; existing 800,000-supply and failure-injection suites pass unchanged.
3. **Contract the durable economy migration for wager and prediction first.** Acceptance: schema and state-transition design identifies unique IDs, escrow ownership, terminal states, retries, and rollback; migration handles existing JSON/persistent views; fault-injection tests at every commit/Discord-send boundary prove no double payout, partial escrow, or lost refund.
4. **Instrument and remove the message hot-path amplification.** Acceptance: baseline captures event-loop lag, DB statements/commits per message, and p95 DB queue time; concurrent summary increments are exact; a cooldown message performs no XP/account existence reads; no whole summary JSON file is rewritten per event; daily output remains compatible.
5. **Prove backup recovery, not just backup creation.** Acceptance: corrupt-newest/valid-previous tests restore the previous valid generation; dashboard/alerts show last successful DB and JSON backup age; an isolated restore drill runs `quick_check`, essential schema checks, and UKP supply/bank invariants; RPO/RTO and rejection diagnostics are documented.

## Changed paths and decisions

No bot implementation, tests, configuration, documentation, dependencies, Git state, deployment, or external system was changed. Only durable audit artifacts were created under `.workflow/ultracode/dashboard-live-audit-test/`. Pre-existing work under `.workflow/ultracode/ultracode-skill-overhaul/` was preserved.

Important delegated claims were verified by the parent against the cited source: bulk-role authorization, wager/prediction/benefits split transactions, `/pay` cap ordering, synchronous backup snapshots, summary hot-path amplification, scheduler registration, backup-generation selection, inbox producer coverage, anti-raid selector limits, and ready initialization.

## Fresh verification evidence

All commands ran from `/Users/ogme01/Documents/Projects/HMS-Victory` on baseline commit `669b10ae6e5a36f459a7f809dc15a9e0acf49354`.

| Command | Exit | Observed result |
|---|---:|---|
| `python3 -m pytest -q` | 1 | Pytest unavailable: `/Users/ogme01/.local/bin/python3: No module named pytest`. No installation attempted. |
| `python3 tests/test_economy_atomicity.py` | 0 | 15/15 passed; injected ledger, bank, bond, and batch failures rolled back as expected. |
| `python3 tests/test_inbox.py` | 0 | 7/7 passed. |
| `python3 -m unittest tests.test_lifecycle_safety -q` | 0 | 28 tests passed. |
| `python3 tests/test_economy_invariant.py` | 0 | 14/18 passed, 4 prediction tests skipped because runtime dependencies were unavailable, 0 failed. |
| `python3 tests/test_badge_rewards.py` | 0 | Passed; reported `user=1625 bank=798375 supply=800000 (conserved)`. |
| `python3 -m unittest discover -s tests -q` | 1 | 30 tests ran; 2 import errors because `PIL` is unavailable (`test_browser_memory`, `test_skyrim`). |
| `bash -n start.sh setup_instance.sh update_bot.sh` | 0 | Shell syntax accepted for all three scripts. |
| `git diff --check -- . ':(exclude).workflow/ultracode/ultracode-skill-overhaul'` | 0 | No whitespace errors in the audit scope. |
| In-app browser inspection of `http://127.0.0.1:8789/` | manual pass | Run title, delegated mode, three packets, verification list, and live activity rendered correctly. |

## Skipped checks

- Full pytest/CI coverage was not run because pytest and Pillow are absent; installing software was forbidden.
- Live Discord command, permission, mobile layout, DM, rate-limit, and anti-raid workflows were not exercised.
- The bot was not started, and no Discord, Gemini, OpenAI, backup channel, production database, systemd service, or other external service was contacted.
- No production database size/query plan, event-loop lag, scheduler runtime, message rate, backup recency, channel ACL, independent host snapshot, or restore-drill evidence was available.
- No migration, administrative script, load test, destructive check, secret decryption, or secret-value inspection was performed.

## Remaining risks

Static evidence proves code structure and local failure-path behavior, not production frequency. The rankings may shift after event-loop/DB telemetry, real backup ages, join-rate baselines, and staff UX walkthroughs are collected. Prediction integrity is a high-confidence code-path concern, but four prediction invariant tests could not load locally. Backup-channel compromise and lack of offsite copies are hardening assumptions because live ACLs and host backups were outside scope. Schema constraints and major command regrouping carry meaningful migration risk and require staged rollout plus rollback plans.

## Follow-up, if any

Use the first two quick wins as a small safety release. In parallel, write the durable economy and DB-worker contracts, instrument current performance, and run a staff tabletop for backup restoration and anti-raid alert thresholds before implementing the larger projects.
