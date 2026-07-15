# Ultracode skill overhaul

## Objective and exclusions

Overhaul Ultracode into a proportional, recoverable, evidence-driven workflow and deliver a complete Claude dashboard prompt.

Overhaul `/Users/ogme01/.codex/skills/ultracode` into a proportional, recoverable, evidence-driven engineering workflow. Deliver a self-contained prompt for Claude to implement the dashboard redesign; do not edit dashboard frontend files in this run.

Excluded: deployment, publishing, commits, pushes, machine-wide installation, unrelated HMS Victory/Skyrim changes, and direct UI implementation.

## Success criteria

- Lean core skill with progressive disclosure and no stale host-tool assumptions.
- Minimal durable artifacts, canonical state vocabulary, legal transitions, recovery semantics, and terminal proof gates.
- Bounded delegation with persistent canonical handles and shared-workspace ownership rules.
- Root-cause debugging, red/green evidence, requirement-before-quality review, and fresh completion proof.
- Optional dependency-free state helper with atomic writes, locking, validation, recovery, and completion enforcement.
- Valid skill metadata and forward-test evidence.
- Claude UI prompt includes exact paths, data contract, visual direction, auto-update behavior, accessibility, and verification.

## Workspace and baseline

- Project workspace: `/Users/ogme01/Documents/Projects/HMS-Victory`
- Installed skill: `/Users/ogme01/.codex/skills/ultracode`
- Starting commit: `843467aba916b1a0fef1e9412770e6605bc38759`
- Pre-existing unrelated dirty paths: `lib/features/skyrim/engine.py`, `lib/features/skyrim/views.py`, `tests/test_skyrim.py`
- Current host: Codex with four total collaboration slots and a shared filesystem.

## Constraints and authority boundary

- Follow the user's `rg`/`fd` search rules and preserve unrelated work.
- Use primary online sources for technical workflow claims.
- Parent owns all installed-skill writes; delegated discovery is read-only.
- Do not modify `tools/web/index.html`, `tools/web/styles.css`, or `tools/web/app.js`.
- Do not claim cross-host behavior that has not been tested.

## Risk and assumptions

Medium risk: this changes a global personal skill, but all changes are local, reversible, and explicitly requested. The UI remains an implementation handoff.

## Design decisions

1. Keep direct, workflow, and delegated modes, but make workflow depth proportional.
2. Replace seven mandatory artifact classes with `run.md`, `state.json`, and `report.md`; packet/result files are optional durable handoffs.
3. Keep `state.json` backward-readable by the existing dashboard while enforcing canonical v2 writes.
4. Map host operations by advertised capability; document the current Codex tools without inventing agent types or close semantics.
5. Add a zero-package `tools/run-state` helper for atomic state mutation, legal transitions, recovery bookkeeping, ownership validation, and proof-gated completion.
6. Use inline contract self-review by default; reserve fresh independent review for medium/high-risk or cross-boundary work.
7. Keep the dashboard as optional read-only observability, not a control plane.

## Packets, dependencies, and ownership

1. `01-superpowers-research` — complete, read-only, no dependencies.
2. `02-current-skill-audit` — complete, read-only, no dependencies.
3. `03-ui-claude-brief` — complete, read-only, no dependencies.
4. `04-skill-overhaul` — parent, depends on 01 and 02, owns `/Users/ogme01/.codex/skills/ultracode` except dashboard frontend files.
5. `05-forward-validation` — fresh read-only agents, depends on 04.
6. `06-integration` — parent, depends on all prior packets.

## Integration order

Research and audit findings inform the lean core, references, and deterministic helper. Fresh forward tests then probe actual behavior without receiving expected diagnoses. The parent reconciles findings, runs validation and smoke checks, writes the Claude prompt, and records remaining limitations.

## Verification plan

- Skill Creator `quick_validate.py`.
- YAML parse and metadata checks.
- Node/shell syntax checks for added and existing launchers.
- Disposable `run-state` lifecycle tests: idempotent init, legal/illegal transitions, recovery/resume, overlap rejection, malformed-state preservation, and completion refusal/success.
- Existing runroom HTTP health/state smoke test without opening a browser.
- Fresh-agent behavior tests and independent final review.
- Confirm dashboard frontend files are unchanged.

## Decisions and recovery notes

The original v1 run artifacts were consolidated into this v2 contract after the audit identified duplicated truth. Research and UI packets made no source edits. The system Python lacked PyYAML; metadata generation was completed with an explicit skill name, and validation uses an ephemeral cached PyYAML environment rather than installing globally.
