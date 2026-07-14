# Integration

## Accepted

- `01-atomic-economy`: accepted after fault injection and casino storage/insolvency separation.
- `02-lifecycle-safety`: accepted after scheduler partial-retry and staged DB/JSON recovery hardening.
- `03-inbox-core`: accepted after page-level read control and DM-safe command logging.
- `04-anti-raid-control`: accepted after backed-up degraded state, versioned permission recovery, and fail-closed join-role integration.
- `05-integration-verification`: accepted after full, CI-subset, and independent-review suites passed.

## Rejected

None.

## Conflicts

- Concurrent Skyrim work was preserved and excluded from this feature review.
- The initial implementation/hardening landed in repository commits during execution outside this agent's commit/push actions; the final anti-raid review fixes remain normal workspace changes unless separately committed.

## Decisions

- Parent owns shared schema and command registration.
- `ALLOW_EMPTY_DB_BOOTSTRAP` remains an explicit first-install-only escape hatch for both missing DB and JSON state.
- Existing confirmed casino-insolvency mint policy remains, but storage faults now propagate and cannot masquerade as insolvency.
- Anti-raid remains fail closed when role enforcement or permission recovery is incomplete.
- No deployment was authorised or performed.

## Final changes

- Atomic UKP bank/user/bulk/tax/pay/bond persistence and fault classification.
- Idempotent process lifecycle and stable scheduler jobs, including partial prediction retries.
- Bounded, validated, rollback-safe DB and JSON recovery plus post-restore prediction reload.
- Durable `/inbox` with private pagination and read/delete controls.
- Private anti-raid control centre with safe transitions, backed-up state, validated role recovery, join quarantine, and staff release.

## Verification still needed

- Manual Discord UI and permission smoke test in a non-production guild before any deployment.

## Remaining risks

- Older cross-store game escrows are not crash-atomic as whole workflows even though their underlying UKP moves are atomic.
- The intentional confirmed-insolvency casino fallback can increase total supply.
- Fail-closed recovery can result in a service-manager restart loop until operators repair credentials or backup state.
