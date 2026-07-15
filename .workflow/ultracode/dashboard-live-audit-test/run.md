# HMS Victory deep optimisation audit

## Objective and exclusions

Produce an evidence-backed ranked backlog of the 15 best bot improvements without modifying bot source.

Inspect the actual bot implementation, tests, configuration, scripts, deployment assets, and documentation, then produce a concrete ranked backlog of the fifteen best improvements. This is a read-only bot audit: no bot source edits, dependency installation, commits, pushes, deployments, external-service calls, or production mutations.

## Success criteria

The final report contains a concise executive summary; exactly fifteen ranked recommendations with impact, effort, confidence, operational risk, evidence paths, dependencies, and classification as quick win or architectural project; five next actions with testable acceptance criteria; exact read-only verification commands and results; and explicit uncertainties, skipped checks, and remaining risks. Important delegated claims are re-opened and verified by the parent against real files.

## Workspace and baseline

Repository root is `/Users/ogme01/Documents/Projects/HMS-Victory`, branch `main`, baseline commit `669b10ae6e5a36f459a7f809dc15a9e0acf49354`. Pre-existing dirty paths are confined to `.workflow/ultracode/ultracode-skill-overhaul/`; they belong to the user and must remain untouched. The audit run may write only under `.workflow/ultracode/dashboard-live-audit-test/`.

## Constraints and authority boundary

All repository inspection and checks are local and read-only except durable audit artifacts in this run directory. Agents may not edit any file, including run artifacts; they return findings to the parent. Do not install software, contact Discord or other external services, read secret values, mutate databases, run migration or administrative scripts, or use commands with write side effects.

## Risk and assumptions

Risk is medium because recommendations touch financial data, moderation, permissions, and recovery design even though execution is read-only. Static evidence and existing isolated tests can support design conclusions, but live Discord, production database, latency, load, backup restoration, and operator workflows will remain unverified unless already represented by local tests or documentation.

## Design decisions

Use three independent read-only explorer packets matching the requested domains. The parent owns repository-wide mapping, evidence reconciliation, ranking, dependency analysis, test execution, report authorship, and all state updates. Rank ideas on qualitative scales: impact and confidence `High/Medium/Low`, effort `S/M/L/XL`, and operational risk `Low/Medium/High`; ranking prioritizes user/data safety and recurring operational benefit over cosmetic breadth.

## Packets, dependencies, and ownership

- `01-architecture-performance`: read-only inspection of startup, scheduler, database access, concurrency, caching, unnecessary work, and module boundaries. No dependencies.
- `02-reliability-security-recovery`: read-only inspection of UKP integrity, idempotency, atomicity, recovery, authorization, limits, anti-raid, failure handling, and observability. No dependencies.
- `03-product-operator-experience`: read-only inspection of Discord commands and UX, inbox/notifications, moderation and anti-raid control, admin ergonomics, automation, and missing capabilities. No dependencies.
- Parent integration: verify important claims, run safe existing checks, deduplicate and rank fifteen recommendations, and produce the final report after all packet handoffs.

## Integration order

Agents run in parallel while the parent maps the repository and verification surface. The parent then validates packet evidence against current files, reconciles overlaps and contradictions, scores candidates, identifies dependencies and unsafe standalone changes, runs the strongest safe local checks, writes `report.md`, validates the durable state, and leaves the runroom open.

## Verification plan

Validate the run schema strictly; inventory repository files and test commands; run existing local tests that do not require external services or write production state; inspect exact cited lines for every shortlisted recommendation; confirm bot source remains unchanged relative to the baseline except pre-existing dirty paths; verify report coverage against every user requirement; and inspect the localhost dashboard in the in-app browser.

## Decisions and recovery notes

This is a new run because the requested slug did not exist. Native agents, inventory, messaging, interruption, and durable canonical handles are available; all delegated work is read-only on a shared filesystem. If interrupted, reconcile live handles and handoffs before re-tasking, preserve accepted results immutably, and never infer completion from an agent message alone.
