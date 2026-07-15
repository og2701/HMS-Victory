# Packet 02: Reliability, security, and recovery

## Objective

Identify and evidence the highest-value improvements in UKP/data integrity, idempotency and atomic operations, disaster recovery, authorization, rate limits, anti-raid systems, failure handling, and observability.

## Binding context and inputs

Inspect the repository at commit `669b10ae6e5a36f459a7f809dc15a9e0acf49354`, including implementation, tests, configuration, scripts, deployment assets, and documentation. Treat financial and moderation boundaries as high consequence and distinguish proven flaws from design hardening.

## Interfaces and dependencies

No packet dependencies. Avoid external systems and production data. Coordinate only through a final structured handoff to the parent.

## Ownership and write scope

Read-only access to the entire repository. No filesystem writes, including this run directory.

## Acceptance criteria

Return 8-12 prioritized candidate improvements, each with exact file and line evidence, threat/failure scenario, expected benefit, effort, confidence, operational risk, dependencies, and a concrete acceptance criterion. Cover backup restoration and observability gaps and note protections already present.

## Do / Do not

Use only safe read-only inspection. Do not edit files, install software, contact external services, run database/admin/migration scripts, decrypt assets, inspect secret values, or expose credentials. Preserve all existing work.

## Verification

Use `rg`, `fd`, file reads, and safe static commands. Run tests only if clearly isolated and non-mutating; report exact command, cwd, exit code, and result.

## Handoff format

Return `DONE`, `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, or `BLOCKED`, followed by summary, ranked candidates, evidence, verification, concerns, and parent actions.
