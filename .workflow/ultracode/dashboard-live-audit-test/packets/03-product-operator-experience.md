# Packet 03: Product and operator experience

## Objective

Identify and evidence the highest-value improvements in Discord commands and UX, inbox and notifications, moderation and anti-raid control, admin ergonomics, useful automation, and missing capabilities.

## Binding context and inputs

Inspect the repository at commit `669b10ae6e5a36f459a7f809dc15a9e0acf49354`, including implementation, tests, configuration, scripts, deployment assets, and documentation. Distinguish workflow gaps from subjective polish and anchor proposals in current command surfaces.

## Interfaces and dependencies

No packet dependencies. Avoid external systems and production data. Coordinate only through a final structured handoff to the parent.

## Ownership and write scope

Read-only access to the entire repository. No filesystem writes, including this run directory.

## Acceptance criteria

Return 8-12 prioritized candidate improvements, each with exact file and line evidence, affected user/operator workflow, expected benefit, effort, confidence, operational risk, dependencies, and a concrete acceptance criterion. Include quick wins, larger projects, and honest UX uncertainties that need live validation.

## Do / Do not

Use only safe read-only inspection. Do not edit files, install software, contact external services, run the bot, run database/admin scripts, or expose secrets. Preserve all existing work.

## Verification

Use `rg`, `fd`, file reads, and safe static commands. Run tests only if clearly isolated and non-mutating; report exact command, cwd, exit code, and result.

## Handoff format

Return `DONE`, `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, or `BLOCKED`, followed by summary, ranked candidates, evidence, verification, concerns, and parent actions.
