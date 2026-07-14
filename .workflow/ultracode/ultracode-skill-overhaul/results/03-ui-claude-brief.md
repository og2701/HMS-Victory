# Result 03-ui-claude-brief: Dashboard architecture

## Status

DONE

## Summary

The dashboard can be redesigned entirely in the three vanilla frontend files. The existing server rereads `state.json` on every `/api/state` request, so no server watcher, framework, bundler, or package is needed for automatic updates.

## Evidence and paths

- Editable frontend: `tools/web/index.html`, `tools/web/styles.css`, `tools/web/app.js`.
- Preserved server: `tools/web-runroom`, `tools/web-runroom.js`.
- Current startup already fetches `/api/state` and polls each second, but also contains overlapping demo/file/folder/playback engines and unnecessary controls.
- The current client ignores server warnings, can overlap requests, substitutes fake demo state on initial failure, and fully rebuilds lists every poll.

## Files changed

None.

## Verification run

The client/server selectors, routes, response envelope, schema fields, startup behavior, and launcher flags were traced directly.

## Concerns and risks

Markup and JS must change together to avoid null dereferences. The new client must preserve tolerant normalization, legacy alias display, dynamic-value escaping, last-good state during reconnect, accessibility, and small-screen wrapping.

## Parent action

Deliver a no-prior-context Claude prompt with an exact edit boundary, three-surface information architecture, dark/orange visual direction, server-only auto-update loop, and browser/curl acceptance checks.
