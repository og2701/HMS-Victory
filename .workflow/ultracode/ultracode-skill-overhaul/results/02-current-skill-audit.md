# Result 02-current-skill-audit: Installed-skill failure modes

## Status

DONE

## Summary

The audit found broken status vocabulary in a real run, obsolete Codex collaboration names, no deterministic resume protocol, excessive duplicated artifacts, and non-replayable verification records.

## Evidence and paths

- The old schema accepted `complete`, while a real run wrote `completed` and agent stage `verified`, causing the terminal runroom to show 0/5 packets.
- The old skill named unavailable `send_input`, `close_agent`, and `agent_type` behavior.
- The old contract lacked transition graphs, retry attempts, dependency/ownership checks, orphan handling, atomic state writes, and terminal invariants.
- Verification used descriptive pseudo-commands rather than exact command, cwd, timestamp, exit code, and observed output.

## Files changed

None.

## Verification run

The audit compared the installed skill/references/tools with `/Users/ogme01/Documents/Projects/HMS-Victory/.workflow/ultracode/hms-safety-inbox-antiraid/` and current Codex tool schemas.

## Concerns and risks

Backward readers should visibly normalize legacy values, while all new writers and validators must reject aliases.

## Parent action

Use a lean core, one canonical run contract, a capability adapter, and one zero-dependency state helper.
