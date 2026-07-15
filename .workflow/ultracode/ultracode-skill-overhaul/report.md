# Report: Ultracode skill overhaul

Status: Complete

## Outcome

Ultracode is now a proportional, recoverable, evidence-driven engineering skill with a lean core, progressive references, capability-based delegation, a deterministic state helper, safer runroom compatibility, and strict terminal proof gates.

The requested dashboard redesign was not implemented here. Instead, `claude-ui-prompt.md` is a self-contained Claude handoff that specifies the exact files, current server contract, simplified dark-orange information architecture, automatic non-overlapping updates, reconnect behavior, accessibility requirements, and acceptance checks. The three dashboard frontend files remain byte-for-byte unchanged.

## Requirement coverage

- Direct, workflow, and delegated modes scale ceremony to task risk and coordination needs.
- Root-cause debugging, explicit red/green proof, requirement-before-quality review, and fresh completion evidence are first-class behavior.
- `tools/run-state` enforces idempotent locked initialization, canonical v2 state, legal transitions, dependency and ownership rules, atomic durable writes, fail-closed recovery, immutable verification contracts, evidence/result integrity binding, and proof-gated completion.
- Legacy `completed`, `done`, and `verified` values remain readable in the terminal runroom while new writers stay canonical.
- The Claude prompt removes unnecessary Live, Demo, Watch, Load JSON, file, folder, playback, and paste controls; limits the UI to three clear surfaces; preserves last-good state; and polls the existing server automatically without overlapping requests.
- Metadata uses contrasting orange `#FF861C` and permits implicit Ultracode invocation.

## Changed paths and decisions

- Replaced `/Users/ogme01/.codex/skills/ultracode/SKILL.md` with a 168-line progressive-disclosure core.
- Reworked six focused references covering approval, debugging, forward testing, host adapters, packet/state schema, and review/verification; removed the obsolete execution-examples reference.
- Added dependency-free `/Users/ogme01/.codex/skills/ultracode/tools/run-state` and `run-state.js`; repaired legacy terminal-runroom normalization and launcher guidance.
- Updated `/Users/ogme01/.codex/skills/ultracode/agents/openai.yaml` with the orange brand color and implicit invocation policy.
- Added this run contract, accepted packet results, final report, and the Claude UI prompt under `.workflow/ultracode/ultracode-skill-overhaul`.
- Kept `tools/web/index.html`, `tools/web/styles.css`, and `tools/web/app.js` untouched so Claude receives a clean, explicit implementation boundary.

## Fresh verification evidence

- Skill Creator validation returned `Skill is valid!` after the final packet transition.
- All JavaScript entrypoints passed `node --check`; all shell launchers passed `sh -n`; `openai.yaml` parsed and asserted `#FF861C` plus implicit invocation.
- Fresh lifecycle replay accepted complete direct and workflow happy paths, kept the main delegated state strict-valid, and rejected missing freshness boundaries, non-ISO dates, malformed state, punctuation, `Not applicable.`, and repeated filler.
- Recovery remained blocked with interrupted owners and proof when artifacts were absent; dependency ordering, canonical aliases, integrity bindings, and live-lock preservation were rechecked.
- Terminal runroom rendered current and legacy state correctly. Web runroom `/api/health`, `/api/state`, and `/` returned HTTP 200 with `cache-control: no-store`, then the localhost server stopped cleanly.
- The independent reviewer returned `READY` with no Critical, Important, or Minor findings for frozen hash `a6fc7c26bdcf7498567d7d3fe173ab52753b21416cf746b8057985100e83e479`; the three frontend hashes remained unchanged.
- All six required checks are fresh and passed at state revision 35.

## Skipped checks

The runtime-fuzz subagent was interrupted by a host policy filter and did not produce a final verdict. Its useful fixtures were preserved, its status is not misreported as success, and the final independent reviewer covered the frozen remediation surface. No visual browser acceptance was run because the user explicitly requested a Claude prompt instead of UI implementation.

## Remaining risks

Cross-host delegation adapters are documented capability mappings and have not been runtime-tested outside Codex. The dashboard will retain its current UI until Claude applies and verifies the supplied prompt; Claude must preserve the server and launcher boundary exactly.

## Follow-up, if any

Give `claude-ui-prompt.md` to Claude from any session with filesystem access to `/Users/ogme01/.codex/skills/ultracode`, then require Claude to run the prompt's static, localhost, responsive, reconnect, and accessibility checks before accepting the UI change.
