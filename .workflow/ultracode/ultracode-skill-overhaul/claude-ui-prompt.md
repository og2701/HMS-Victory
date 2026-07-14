# Prompt for Claude: overhaul the Ultracode dashboard

You are working on a local, dependency-free dashboard for the installed Ultracode Codex skill. You have no prior context, so read this prompt completely and inspect every named file before editing.

## Objective

Completely redesign the Ultracode runroom frontend into a compact, exceptionally clear live status dashboard. It should feel calm and operational rather than game-like: near-black/charcoal surfaces, strong white text, and one contrasting orange accent. It must update itself from the server continuously without reloads or user controls.

The current UI is too busy and has too many components, colours, animations, and modes. Remove unnecessary controls and machinery, especially Live, Demo, Watch Folder, Load JSON, playback/pause, drag/drop, paste-to-load, client-side demo state, and folder/file pickers.

## Repository and exact paths

Installed skill root:

`/Users/ogme01/.codex/skills/ultracode`

You may edit only these three frontend files:

1. `/Users/ogme01/.codex/skills/ultracode/tools/web/index.html`
2. `/Users/ogme01/.codex/skills/ultracode/tools/web/styles.css`
3. `/Users/ogme01/.codex/skills/ultracode/tools/web/app.js`

Read but do not edit:

- `/Users/ogme01/.codex/skills/ultracode/tools/web-runroom`
- `/Users/ogme01/.codex/skills/ultracode/tools/web-runroom.js`
- `/Users/ogme01/.codex/skills/ultracode/references/packet-schema.md`
- `/Users/ogme01/.codex/skills/ultracode/SKILL.md`

Do not add a framework, build system, package, dependency, font download, icon package, server migration, or generated assets. This must remain plain semantic HTML, CSS, and browser JavaScript served verbatim by the existing Node server.

Do not edit any HMS Victory project files. Do not commit, push, publish, or deploy.

## Existing server contract

The shell launcher executes `tools/web-runroom.js`.

Launch forms:

```sh
/Users/ogme01/.codex/skills/ultracode/tools/web-runroom <state.json-or-run-directory>
/Users/ogme01/.codex/skills/ultracode/tools/web-runroom --demo
```

Relevant flags:

- `--host <host>`; default `127.0.0.1`
- `--port <port>`; default `8787`
- `--open` / `--no-open`
- `--demo`
- `--help`

Routes:

- `GET /api/health` returns `{ "ok": true }`.
- `GET /api/state` returns `{ ok: true, source, state }`.
- If the state file is temporarily missing or invalid, `/api/state` still returns HTTP 200 with `{ ok: true, source, warning, state }`; `state` is a safe synthetic waiting run.
- Static assets have `cache-control: no-store`.
- For a directory input, the server reads `<directory>/state.json`.
- The server rereads and parses `state.json` on every `/api/state` request. No file watcher, WebSocket, SSE endpoint, or server change is needed.
- In server demo mode, state advances when `/api/state` is requested.

Treat the server as the only data source. The client must not contain its own demo state or alternate file/folder inputs.

## State data to support

Be tolerant of partial/missing optional fields and empty arrays. Never throw because a waiting run is incomplete.

Core run fields:

- `schema_version`, `run_id`, `revision`
- `title`, `slug`, `goal`
- `created_at`, `updated_at`, `heartbeat_at`
- `status`, `phase`, `mode`
- `risk.{level,reasons}`
- `approval.{required,granted,scope,notes}`
- `delegation.{native_agent_available,native_agent_used,host,capacity,notes}`
- `resume.{attempt,last_recovered_at,last_resumed_at}`
- `blockers[]`

Packets:

- `id`, `title`, `status`, `status_reason`
- `owner`, primary `agent_handle`, optional `agent_handles[]`, `dependencies[]`, `write_scope[]`
- `acceptance_criteria[]`, `result_path`, `attempt`, timestamps

Agents:

- `id`, `task_name`, `label`, `role`, `status`, `packet`, `stage`, `last_seen_at`

Verification:

- `verification.status`
- checks with `name`, `claim`, `command`, `cwd`, `required`, `status`, `run_at`, `exit_code`, `observed_result`, legacy `evidence`, and `evidence_path`

Events:

- canonical `at`, `actor`, `type`, `message`
- legacy-compatible `time`, `agent`, `type`, `message`

Canonical run statuses:

`planning`, `waiting_for_approval`, `executing`, `integrating`, `verifying`, `complete`, `blocked`, `cancelled`

Canonical packet statuses:

`pending`, `in_progress`, `complete`, `blocked`, `interrupted`, `skipped`

Canonical check statuses:

`pending`, `in_progress`, `passed`, `failed`, `interrupted`, `skipped`

Compatibility readers must visibly normalize old values:

- `completed`, `done`, and `verified` to `complete` for runs/packets/agents
- `running` to `executing`
- `completed`, `complete`, and `verified` to `passed` for checks
- legacy agent stage `verified` to `complete`

Humanize raw labels: `in_progress` becomes `In progress`, `waiting_for_approval` becomes `Waiting for approval`, and so on.

## Information architecture: at most three principal surfaces

Do not create a wall of cards. Use a simple DOM reading order and at most these three main surfaces:

1. Compact header
   - Small Ultracode wordmark or restrained orange mark made with CSS/text.
   - Run title as the primary label.
   - Human-readable run status and mode.
   - Connection state: `Live`, `Reconnecting`, or `Waiting`.
   - Last successful update time.
   - No controls or buttons.

2. Overview
   - One compact five-step rail: `Plan → Explore → Build → Verify → Ship`.
   - Show current/completed/blocked steps with text and restrained semantic styling; never colour alone.
   - In the same surface, show only the three useful totals: packets completed/total, required checks passed/total, and active/known agents.
   - If approval is pending, recovery is active, or blockers exist, surface the most important condition here without adding another permanent card.

3. Details region
   - Desktop: two readable columns; mobile/tablet: one column in DOM order.
   - `Work`: packets followed by verification checks as compact rows, with title/claim, owner or command, status, and only useful secondary detail. Long paths/commands must wrap.
   - `Activity`: latest events, newest first or with an obvious chronological treatment. Keep the list bounded (for example latest 20–30) and preserve its scroll position when data updates.

Use explicit empty states such as `No packets yet`, `No verification checks registered`, and `No activity yet`.

Use one conditional inline banner for server warnings or reconnecting state. It must not occupy space when absent.

## Visual direction

- Background: near-black, e.g. `#09090b` or `#0b0b0d`.
- Surfaces: restrained charcoal, e.g. `#121216` and `#18181d`.
- Primary text: off-white with high contrast.
- Secondary text: readable neutral grey, not faint grey-on-black.
- Primary accent: orange around `#ff861c`; use it for current progress, focus, and small key accents.
- Use restrained green only for success and restrained red only for failures/blockers.
- No rainbow agent colours, multicolour stage gradients, glass blur, heavy glow, giant shadows, decorative grid background, pixel sprites, bobbing/shaking, or continuous animation.
- No oversized headings or excessive uppercase/eyebrow labels.
- Use spacing, type weight, one-pixel borders, and alignment to create hierarchy.
- Aim for a serious operations tool: understandable in five seconds, useful at a glance, and comfortable to leave open.
- Use system fonts; a compact monospace stack is appropriate only for ids, times, commands, and counts.
- Provide a `prefers-reduced-motion: reduce` rule even though there should be no continuous motion.

## Automatic update behavior

Implement a single server polling loop:

1. Fetch `/api/state` immediately on page load with `cache: "no-store"`.
2. After each request completes, schedule the next request with `setTimeout` at roughly one second. Do not use an overlapping `setInterval`.
3. Keep the last good run visible during transient failures.
4. Change connection state to `Reconnecting`, display one concise conditional message, and retry automatically with a small bounded backoff. Do not replace the run with fake client data.
5. Surface `payload.warning` while still rendering the server-supplied waiting state.
6. Pause or slow routine polling while the document is hidden; fetch immediately on `visibilitychange` to visible and on window focus.
7. Prevent duplicate loops when focus/visibility events fire.
8. If the meaningful payload has not changed (prefer `revision`/`updated_at`, with a safe fallback), avoid rebuilding lists or causing visual flicker.
9. Update the DOM without a full page navigation or reload.
10. Preserve the Activity scroll position when an unchanged or incrementally extended event list renders.

Use an `AbortController` or equivalent timeout only if it simplifies robust cleanup. Keep the implementation easy to audit.

## JavaScript and security constraints

- Rewrite the client around one normalized state and one polling engine. Delete all client `DEMO_BASE`, `DEMO_STEPS`, playback timer, file input, folder picker, drag/drop, and paste listeners.
- Delete measurement and absolute-position agent animation code.
- Keep normalization in small, named functions.
- Render dynamic values with `textContent` and DOM creation where practical. If any HTML strings remain, escape every dynamic value.
- Invalid dates must display a neutral fallback such as `Unknown`, never `Invalid Date`.
- Missing arrays become empty arrays. Missing nested objects get safe defaults.
- Do not log state payloads or user data to the console.
- No polling request may overlap another.
- Do not hide operational errors; show the concise banner and retain last-good information.

## Responsive and accessibility requirements

- No horizontal page scroll at 320px width.
- Verify layouts at 1440×900, 1024×768, 390×844, and 320×568.
- Desktop: compact overview plus two-column Work/Activity details.
- Tablet/mobile: single column in semantic DOM order; stage rail wraps or becomes a compact vertical sequence.
- Apply `overflow-wrap: anywhere` to ids, result paths, write scopes, commands, evidence, titles, and messages.
- Meet WCAG AA contrast: at least 4.5:1 for normal text and 3:1 for large text, meaningful boundaries, and focus indicators.
- Never convey status by colour alone; always include visible text.
- Use semantic `header`, `main`, `section`, headings, lists, and `<time datetime>` where appropriate.
- Limit `aria-live="polite"` to connection/run-status changes. Do not announce the full page or every one-second refresh.
- Keep keyboard focus visible for any naturally focusable content. There should be no unnecessary interactive elements.
- No hover-only information.

## Remove completely

The final frontend must contain no implementation or visible copy for:

- Live button
- Demo button
- Watch Folder
- Load JSON
- play/pause button
- hidden file input
- directory picker
- drag/drop loading
- paste JSON loading
- client-side demo feed
- large animated arena/lanes/path
- pixel agent sprites or agent-position measurement
- drop hint overlay

Removing only the markup is insufficient: remove the associated state, timers, selectors, functions, and event listeners so there are no null dereferences or dead code paths.

## Required verification

First run static checks:

```sh
node --check /Users/ogme01/.codex/skills/ultracode/tools/web/app.js
node --check /Users/ogme01/.codex/skills/ultracode/tools/web-runroom.js
sh -n /Users/ogme01/.codex/skills/ultracode/tools/web-runroom
/Users/ogme01/.codex/skills/ultracode/tools/web-runroom --help
```

Confirm removed machinery has no remaining matches:

```sh
rg -n 'data-action|Watch Folder|Load JSON|watchFolder|loadFile|DEMO_BASE|DEMO_STEPS|showDirectoryPicker|data-file-input|drop-hint|agent-layer|path-line' /Users/ogme01/.codex/skills/ultracode/tools/web
```

Expected result: no matches.

Start the real server without opening a browser; if port 8787 is occupied, choose another explicit free port and use it in the following commands:

```sh
/Users/ogme01/.codex/skills/ultracode/tools/web-runroom --demo --no-open --port 8787
curl -fsS http://127.0.0.1:8787/api/health
curl -fsS http://127.0.0.1:8787/api/state
curl -fsSI http://127.0.0.1:8787/
```

Request `/api/state` twice a few seconds apart and confirm demo state changes. Then test a real v2 run:

```sh
/Users/ogme01/.codex/skills/ultracode/tools/web-runroom /Users/ogme01/Documents/Projects/HMS-Victory/.workflow/ultracode/ultracode-skill-overhaul --no-open --port 8787
```

In an actual browser, verify:

- initial state appears without clicking anything;
- `/api/state` repeats while the page stays loaded and there are no full-page navigations;
- an atomic `state.json` update appears within roughly two seconds without reloading;
- stopping the server keeps last-good data visible and shows `Reconnecting`;
- restarting the server reconnects automatically;
- a server `warning` is visible without crashing the waiting state;
- no console errors occur;
- no removed controls or dead spaces remain;
- legacy `completed`/`verified` data displays as complete and counts correctly;
- 1440, 1024, 390, and 320-wide layouts are readable with no horizontal overflow;
- reduced-motion and keyboard/accessibility checks pass.

If browser automation is available, use it. Capture screenshots at desktop and mobile widths for your own visual QA, but do not add screenshot assets to the skill.

## Definition of done

- Only the three allowed frontend files changed.
- The UI has at most three principal surfaces and no unnecessary buttons/modes.
- It is dark, restrained, and clearly orange-accented.
- It auto-connects and auto-updates from the existing server with no reload or user action.
- Reconnect/warning/empty/partial/legacy states are safe and understandable.
- Responsive/accessibility and all static/server/browser checks above pass.
- The final code is substantially smaller and easier to understand than the current frontend.

In your final response, list the three changed files, summarize what you removed and the new information architecture, report every verification command and browser scenario actually run, and state any skipped check or remaining risk. Do not claim a check passed unless you ran it after the final edit.
