# Presentation handoff

Implemented in `lib/features/skyrim/views.py`, new `presentation.py`, and `tests/test_skyrim_readability.py`.

- Compact live combat: numeric HP, one short outcome, intention/counter, legal healing/exit controls, Guard availability, Home and private Inspect. Inspect contains critical odds, guard risk, pacts, history and character details.
- All private panels share summary/detail paging and short button rows. Maximum three buttons and 30 total label characters per row, otherwise split. Generic summaries cap at 700 characters; Character, Holdings, Notice Board and location selection have curated summaries. All full text remains in Inspect.
- Hub has a contextual goal, direct resume link and guided first-adventure entry. Completed delves show outcome, gold/material gains/losses, skills, unlocks and task progress; Next goal opens its actual target privately.
- Launch holds the sessions lock through prepare/post/commit, acknowledges before I/O, consumes nothing on post failure and invalidates an uncommitted board on conflict/save failure. Success includes an Open adventure link.
- Hunt exposes Attack/Expose/Protect before each march, a compact public report with support attribution, and an inspectable private report. Pit/duel boards use numeric health, short outcomes, Home/Inspect and open-board links.
- Faction promotion progress/claiming and explicit rebirth inheritance selection are connected to progression helpers. Irreversible retirement details remain fully visible.
- Vigor healing uses delve maximum; event exits respect Clavicus; bank/flee labels show the actual amount. Replace-adventure screen explains settlement and disables replacement under the no-exit pact.

Validation: 14 async unittest checks pass under `scripts-local/test_skyrim_isolated.py test_skyrim_readability.py`. Includes actual LayoutView payloads for 17 private menus on both novice and advanced profiles, row/component/text limits, inspection through every detail page and back, persistent public IDs, launch failure/conflict callbacks, Vigor/pact/flee controls, private goal routing, onboarding, arena navigation, inheritance and promotions. All files redirect to a temporary directory; every Discord interaction is mocked. `git diff --check` passes.

Parent integration/verification: complete canonical `E.tutorial_available/start_tutorial` and `E.wb_march(role=...)` hooks, full-suite checks, and labelled approximate 360px/desktop previews from actual payloads. No live Discord client execution, bot connection, deployments, commits or player-data writes were performed.
