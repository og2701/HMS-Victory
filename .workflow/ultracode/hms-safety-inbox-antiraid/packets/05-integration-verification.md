# Packet 05-integration-verification: Shared integration and proof

## Objective

Integrate packet outputs into shared schema/registration paths, wire remaining inbox producers, resolve conflicts, and verify the full change set.

## Context

This packet starts after bounded agent handoffs are available.

## Sources

- all changed files
- `database.py`
- `lib/bot/setup_commands.py`
- relevant tests and CI workflow

## Ownership

Parent integrator.

## Do

- Review every agent diff rather than trusting summaries.
- Add notification schema and indexes centrally.
- Register `/inbox` and anti-raid control commands with existing staff checks.
- Wire badge/moderation inbox events after lifecycle edits settle.
- Run focused and full practical verification.
- Request independent review before completion.

## Do not

- Revert user-owned untracked assets.
- Commit, push, or deploy.

## Expected output

Integrated implementation, evidence, final report, remaining risks.

## Verification

All checks in `state.json`.

## Handoff format

Final report and concise user-facing summary.
