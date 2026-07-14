# Packet 03-ui-claude-brief: dashboard architecture brief

## Objective

Map the existing dashboard and server so the parent can give Claude a complete implementation prompt without modifying UI code in this run.

## Context

The user wants a simple, self-explanatory, dark dashboard with contrasting orange accents, few components, no Live/Demo/Watch/Load JSON controls, and automatic updates without reloads.

## Sources

- `/Users/ogme01/.codex/skills/ultracode/tools/web/index.html`
- `/Users/ogme01/.codex/skills/ultracode/tools/web/styles.css`
- `/Users/ogme01/.codex/skills/ultracode/tools/web/app.js`
- `/Users/ogme01/.codex/skills/ultracode/tools/web-runroom`
- `/Users/ogme01/.codex/skills/ultracode/tools/web-runroom.js`
- `references/packet-schema.md`

## Ownership

Read-only.

## Do

- Explain server/data flow and current polling behavior.
- List exact files Claude may edit and files it must preserve.
- Identify unnecessary controls and simplification opportunities.
- Define responsive, accessible, auto-update acceptance criteria.
- Include validation commands Claude can run.

## Do not

- Edit UI files.
- write the final user-facing prompt.
- assume a framework migration is needed.

## Expected output

Prompt-ready architecture context, constraints, acceptance criteria, and risks.

## Verification

Cite exact paths, functions, DOM IDs/classes, and server endpoints.

## Handoff format

Concise Markdown suitable for parent integration.
