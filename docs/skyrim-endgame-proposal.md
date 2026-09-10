# HMS Victory Skyrim: an endgame beyond the fifth legend

Design proposal · 10 September 2026 · reviewed against local commit `c6ac974`

**Revised recommendation: expand the existing adventures and Hall without adding hub or combat buttons.** Allow retirement to continue beyond Rank 5 without a final rank cap, keep permanent boon rewards bounded, and introduce encounters and connected stories that use the controls players already know.

Historical design proposal. The agreed compact scope is now implemented locally; see [implementation and verification](skyrim-endgame-implementation.md) for the final rules and shipped content. Parked avenues below remain proposals. Player statistics came from the supplied brief; production profiles and player activity were not inspected.

## Scope decision: keep the game uncluttered

The user's clarification makes interface simplicity a hard requirement. The recommended scope is now three extensions to existing features:

1. **Richer adventures:** new enemy behaviours, routes, and boss phases using existing attacks, Guard, Shout, and contextual room choices. A clearly labelled optional challenge occupies an existing adventure-offer slot; ordinary adventures remain available. There is no Trials menu or new preparation screen.
2. **Repeatable retirement, further Hall progress, and boons:** players at Rank 5 can continue retiring through the existing Hall and confirmation flow. One current deed appears in the existing next-goal line, with progress and rewards recorded automatically. Three later milestones each award a new passive boon. The existing Hall shows their effects and history. No separate Chronicle dashboard, chapter picker, pinning workflow, claim button, or boon-equipping screen.
3. **Connected faction stories:** the current faction objective can lead into a short authored story using ordinary adventure rooms. No separate quest-management screen or additional daily checklist.

**Interface budget:** zero additional hub buttons, zero additional persistent combat buttons, no new currency or inventory category, and no mandatory extra clicks before entering an adventure. New room decisions use two or three contextual choices that replace the room's current controls. Show only the current objective and one concise encounter cue; reuse Inspect for optional detail.

Relic equipment, draft-based Apocrypha runs, research projects, exhibition management, shared campaigns, and a separate proving circuit are **parked alternatives**, not a committed roadmap. The broader avenue descriptions below remain background design material; this scope decision takes precedence wherever they introduce extra management or screens. Apocrypha could still appear later as an adventure setting with the existing controls.

## 1. What the current game actually needs

The strongest diagnosis is **exhaustion of meaningful goals**, rather than simply a shortage of levels. Someone who has killed Alduin 38 times probably needs a different decision and a new achievement to pursue more than a 39th version of the same reward.

The checkout also differs from the brief in several useful ways:

| Verified implementation | Design implication |
| --- | --- |
| Legacy stops at five, but there are **eight available boons**. Players select from seeded offers; five retirements do not collect all eight. | Raising the cap changes build power and eventually exhausts the boon pool again. |
| Level growth and Legendary skill resets have no explicit numerical ceiling. Doctrine slots stop at the available choices. | An uncapped counter already exists; more counters alone will not solve engagement. |
| The Soul Cairn already scales indefinitely. Legacy reduces its drain by 12% per rank, capped at 75% resistance. | Raising Legacy from five to six would increase resistance from 60% to 72%; it is a material balance change. |
| Alduin's combat Echo stops at four, while the dragon-kill requirement continues increasing with lifetime Alduin kills. | Later rematches increasingly add preparation requirements without a new combat pattern. |
| Combat already includes telegraphed intentions, Guard, contextual counters, and connected story consequences. | Build challenges around decisions already supported by the game. |
| Factions already have promotion missions; hunts already support Attack, Expose, and Protect. | New faction and cooperative content must go beyond another checklist or another shared health bar. |
| There are 22 location definitions, including the Soul Cairn; the collection counts 21 clearable places. | The brief's 21 is a collection count, not the total location count. |
| Hubs are private, but adventures are posted as public, owner-controlled boards. The UI uses Components V2. | Preserve private preparation and readable public adventure progress. |

Sources: [boons and Legacy constants](/Users/ogme01/Documents/Projects/HMS-Victory/lib/features/skyrim/data.py:1629), [level calculation](/Users/ogme01/Documents/Projects/HMS-Victory/lib/features/skyrim/data.py:1681), [doctrines and Legendary skills](/Users/ogme01/Documents/Projects/HMS-Victory/lib/features/skyrim/engine.py:3535), [Cairn resistance](/Users/ogme01/Documents/Projects/HMS-Victory/lib/features/skyrim/engine.py:560), [Alduin gates](/Users/ogme01/Documents/Projects/HMS-Victory/lib/features/skyrim/engine.py:2924), [combat and stories](/Users/ogme01/Documents/Projects/HMS-Victory/lib/features/skyrim/combat.py:1), [promotions and hunt support](/Users/ogme01/Documents/Projects/HMS-Victory/lib/features/skyrim/progression.py:149), [public adventure launch](/Users/ogme01/Documents/Projects/HMS-Victory/lib/features/skyrim/views.py:1123).

## 2. Progression principles

Keep permanent combat power bounded. Vertical progression should primarily mean earning access to harder **optional** encounters and learning to handle them. Earlier dungeons should continue to serve as familiar, easier content; avoid raising their difficulty automatically because a player completed another Chronicle.

Horizontal progression should unlock equipment alternatives, routes, encounter knowledge, story endings, and forms of recognition. A new option earns its place when a player sometimes chooses it and sometimes leaves it behind.

Use three reward layers:

| Horizon | Desired reason to play | Example |
| --- | --- | --- |
| One sitting, roughly 3–8 minutes | Finish something and make a consequential choice | Clear a trial, recover a quest fragment, choose a dangerous route. |
| Several sittings, roughly 1–3 weeks | Complete a connected objective | Finish a faction case, assemble a relic, earn a trial's mastery medal. |
| Several weeks or months | Develop a recognisable account history | Complete Chronicle chapters, curate a trophy hall, improve seasonal records. |

These are pacing targets to measure, not claims about present session length. Timers should not be the main mechanism that makes an objective take weeks. Players should be able to take breaks without losing permanent progress or already-earned rewards.

## 3. Avenue A: the Chronicle of Legends

**Purpose:** let players continue retiring after Rank 5 and pursue further Hall deeds, while keeping retirement optional.

**Loop.** After reaching Legacy 5, the existing Hall continues with a sequence of deeds. The next relevant deed appears in the current next-goal line. Playing ordinary adventures and clearly labelled challenge variants records progress and awards completed chapters automatically. The Hall retains the history without asking players to select, pin, or claim anything. A chapter asks for different kinds of accomplishments rather than a rising total of identical kills.

For the first chapter, track three concrete proofs from the new adventure variants: defeat the Rune Warden, finish the Lost Caravan with its scout rescued, and recover the Sealed Vault's ledger through its quiet route. All three routes have guaranteed opportunities to attempt their objective. Later chapters can accept any three deeds from five, including combat, exploration, and service alternatives, with eligible play counting automatically.

**Progression.** Preserve each account's earned rank and existing boons, then allow `legacy.rank` to increase with every further retirement. Add a separate chapter count and catalogue of unique proof IDs. Award an epithet and an automatically recorded Hall entry. Three designated post–Rank 5 milestones additionally award one new passive boon each: up to three extra boons alongside the player's five selected Legacy boons. Each milestone names its reward in advance and grants it automatically, once. These are new endgame boons; the three unchosen options in the original eight-boon pool are not automatically granted.

The extra boons should provide bounded situational benefits, such as a second roll on the first failed lockpick of an adventure. That example and the remaining effects require balance work before implementation. Avoid recurring upgrades to the same boon or a permanent increase in base damage, health, stamina, or the Legacy-based Cairn resistance. Once earned, the boons work automatically; there are no active slots, swapping chores, or extra combat controls. Later chapters continue to unlock challenge variants, stories, and recognition after the three-boon cap.

Chapter completion never silently retires the character. Eligible players below Rank 5 may bank trial proofs in advance; reaching Rank 5 enables chapter and boon awards without requiring those feats again.

**Retirement after completing the original Hall.** Remove the final retirement lock: Rank 5 can become 6, then 7, and continue indefinitely. Use the existing retirement action and explicit reset confirmation. Apply the established character reset and inheritance rules, while retaining all earned boons, collections, homestead ownership, lifetime records, and new Hall progress. The current character is never reset by migration or by completing a deed.

For post–Rank 5 lives, use a repeatable qualification that requires rebuilding the current character and defeating Alduin during that life. A fixed level threshold of 32—the current fifth-retirement threshold—is an initial balance candidate. Do not keep adding three required levels forever, or allow old lifetime Alduin kills to qualify every subsequent life. Additional retirement must remain available after all three extra boons and all currently authored deeds have been earned; a fresh unique chapter is not an entry requirement. Every retirement still records an epitaph and increases the visible Legacy rank. Further power rewards stop at the stated cap.

Bound the associated access requirements as well. For post–Rank 5 characters, base Alduin's dragon requirement on current-life progress with a finite rematch escalation, rather than an ever-growing lifetime kill count. Preserve lifetime counters for records. Freeze the exact thresholds after simulations; a nominally unlimited retirement loop must not become inaccessible to the oldest accounts. Initialise current-life baselines from a validated latest retirement epitaph where possible: it already records lifetime Alduin and dragon totals. When that evidence is unavailable, preserve all old progress and require a newly observed qualifying victory rather than inventing one.

**Separate rank from power.** The first five ranks retain their existing rules. For old rank-scaled combat effects such as Cairn drain resistance, use a capped effective rank of five even when the displayed Legacy rank is higher. Extra Hall boons are independently capped and never re-awarded on reset. Preserve the original five-rank Hall badge threshold and one-time payout. In the Hall, replace the terminal “complete” state with the next retirement's requirements; completed chapters remain visible achievements.

The catalogue can grow over time, but a repeated deed must not award the same proof twice. Display repeat records separately. This requires continued small content additions; it does not manufacture infinite novelty from one template.

**Integration.** Add chapter, deed, and endgame-boon definitions to `data.py`; implement proof evaluation in a focused proposed `chronicles.py`; invoke it from committed gameplay events. Extend `progression.next_goal()` and the Hall panel. Store progress and awarded boon IDs in `profile["chronicle"]`, explicitly preserved through retirement. Keep the existing retirement boon pool and rank-dependent formulas separate. Update `retire_ready()`, `retire_level_needed()`, `retire()`, and the Hall confirmation to support a post–Rank 5 retirement without a new boon selection; the current code requires a valid offered boon and would otherwise block it. Persist new life baselines with the reset, and make a repeated confirmation idempotent. Audit rank consumers and the Hall badge predicate so removing a retirement cap does not remove a power cap or move an existing badge threshold. Save boon awards with their milestone completion, and persist any once-per-adventure usage in the board so a restart cannot replenish it. Historical counters may backfill only deeds they prove; an old kill total cannot prove a particular style, route, or no-potion clear.

**Scope:** medium. Tracking and presentation are straightforward; credible deeds depend on the trial content below. This should be the organising layer of the expansion, not a new standalone grind.

## 4. Avenue B: authored mastery trials

**Purpose:** make existing combat knowledge useful in situations veterans have not solved already.

**Loop.** Choose a clearly labelled challenge in the existing adventure offers and play a short authored dungeon with the current character and controls. Its defining rule is visible on the offer; starting it requires no separate preparation screen. Earn a completion seal first; optional mastery objectives encourage replay. Unlock basic variants after an Alduin victory, with harder variants available later. Rank 4 players can participate before completing the Hall. “Trial” is an internal content category, not an additional player-facing menu.

Three launch examples:

| Trial | New decision | Optional mastery objective |
| --- | --- | --- |
| Rune Warden | After taking a landed hit, its ward resists that attack style until a different style lands. Display the ward before the next action. | Break each of its three ward states. |
| Lost Caravan | Choose between a supply cache and freeing a scout whose help counters a named boss attack. The rescue opportunity always appears. | Rescue the scout and successfully use their protection. |
| Sealed Vault | Choose a quiet route with locks and negotiation or a longer combat route. Failed checks create a recoverable complication. | Recover the ledger without raising the final alarm. |

**Progression.** Use finite completion and mastery medals, then curated combinations of already-learned rules. Bronze means completion; higher medals reward an explicit decision objective. Avoid making essential progression depend on an unwounded run or other perfect sequence of lucky rolls. Optional constraints should not contradict one another—for example, a mandatory style-switching ward cannot coexist with a single-style restriction.

Trials spend one existing delve charge. Reward XP and septims within a comparable normal-run budget; first-completion recognition supplies most of the novelty. Do not let trial multipliers multiply every existing pact reward. Free practice can use a temporary character sheet, with no currency, skill practice, drops, or persistent task credit.

**Integration.** Extend room state and deterministic intent rules in `combat.py`, with authored layouts and compatibility tags in `data.py`. Add trial preparation and eligibility to `sessions.py`, and serialise a trial ID, rules version, frozen loadout, objective state, and modifiers in `Delve`. Evaluate achievements after the outcome commits. Reuse the existing attack, Guard, choice, and Inspect controls.

**Scope:** medium. Existing pacts and Stirred difficulty already cover numerical pressure; the new work is authored mechanics and reliable objective tracking.

## 5. Avenue C: Apocrypha and the Black Books

**Purpose:** provide a replayable mode in which a run develops its own build.

Apocrypha's forbidden library and dangerous knowledge suit bargains, branching pages, and knowledge gained at a price. This setting follows established Elder Scrolls themes; the specific books and rules proposed here are original minigame content, not claims about canonical quests. [Official Apocrypha description](https://www.elderscrollsonline.com/en-us/updates/chapter/necrom)

**Loop.** Enter an unlocked book, traverse six to eight short rooms, and choose one of three temporary gifts at two chapter breaks. Each gift states its benefit and cost. Before the finale, choose to leave with gathered rewards or continue for the book's completion seal.

Examples: the first fire hit burns away a ward, but potions restore no health in the next room; a spectral guide reveals the next branch, but choosing the treasure branch increases the final guardian's health. Costs must be concrete and previewed, not a hidden curse table.

**Progression.** Each book introduces a distinct ruleset and ending. Completing it unlocks a second route and adds options to later drafts. Keep the number of active gifts fixed so a larger collection expands choice rather than stacking permanent power. Start with one book and a small gift pool before commissioning a large content set.

Unlock the first book after Alduin through a guaranteed short quest, not an ultra-rare drop. Entry uses one normal delve charge. On failure, temporary gifts disappear; existing character progression follows the current mode's banking rules, and unbanked rewards follow an explicit entry preview. No permanent skill loss or confiscation of owned gear.

**Integration.** Add a proposed `apocrypha.py` for draft and branch rules, data definitions for books/gifts, and `kind="apocrypha"` in session preparation. Persist the offered choices, chosen gifts, room sequence, and resource state so reopening cannot reroll a draft. Apply gifts through a shared combat-rules interface rather than unrelated conditionals scattered through views. Use three short choice buttons at chapter breaks.

**Scope:** large. This has strong replay potential but needs encounter, gift-interaction, and reward balancing. It is the second major release, not the quickest response to the current cap.

## 6. Avenue D: relics and deliberate loadouts

**Purpose:** let maxed characters develop different identities without introducing Tier 7–20 gear.

**Loop.** Choose a relic research project, complete a short set of visible objectives, and spend existing septims/materials to finish it. Equip one relic before an eligible adventure. Preview its tradeoff and lock the choice for that run.

Prototype alternatives:

| Relic | Benefit | Cost |
| --- | --- | --- |
| Runic Buckler | The first successful counter-Guard creates a stronger opening. | One fewer starting potion in that run. |
| Ashen Lens | The first landed fire hit against each warded guardian removes its ward. | Lose the normal Blade interruption benefit while equipped. |
| Pilgrim's Knot | One failed negotiation can reveal a second, costed peaceful route. | Reduced final septim reward. |

These are candidates for testing, not final balance commitments. A benefit that always dominates its cost should be redesigned. Begin in trials and Apocrypha, where effects and comparisons can be contained; expanding into ordinary delves is a later balance decision.

**Progression.** Unlock alternatives and cosmetic variants. Keep one active slot and a fixed upgrade ceiling. Use guaranteed project progress with a disclosed finite requirement, not duplicate drops, random stat rolls, or another spendable currency. Existing boons and doctrines remain earned benefits; do not silently make them mutually exclusive.

**Integration.** Add `relics` and `selected_relic` profile fields and snapshot the selection into the adventure. Proposed `loadouts.py` resolves eligibility, tradeoffs, and interactions with potions, pacts, companions, doctrines, and gifts. Persist ownership through retirement. Crafts validate costs and credit ownership in the same profile write.

**Scope:** medium to large because combinations drive testing cost. More shouts can later use this same approach—alternative selected techniques drawing on the existing Voice resource—rather than another independent combat skill tree.

## 7. Avenue E: faction story campaigns

**Purpose:** turn faction membership into a story that continues after promotion.

**Loop.** A high-ranking member accepts a three-to-five-stage case. Each stage is a short adventure or a small objective attached to an existing delve. A choice changes a later encounter, ally, or ending. The next stage remains available until played.

Examples: the Thieves Guild recovers a falsified ledger through bribery, stealth, or a dangerous vault; the College investigates a sealed ruin and chooses whether to contain or study its ward; the Companions hunt a creature while deciding how much danger to draw away from a settlement.

**Progression.** Award campaign titles, companion appearance variants, homestead displays, and a relic research unlock. Give comparable rewards to different endings so one does not become the economically compulsory answer. Repeat a case to discover another ending, with the large completion grant limited to its first award. Retired characters retain discovered chapters and endings; access to an unfinished faction commission can require regaining membership without erasing its history.

Faction-specific rank names would also improve atmosphere: the current shared ladder calls every faction's top rank “Harbinger.” Keep rank IDs and existing achievements stable while improving display names.

**Integration.** Reuse promotion eligibility in `progression.py` and story-consequence patterns in `combat.py`. A proposed `quests.py` stores `quest_id`, `stage_id`, choices, completed stages, and reward receipts. Add guaranteed quest entry points to the location picker; do not make a required chapter wait on the daily location rotation. Extend the Factions panel with one active-case summary and a Continue button.

**Scope:** medium engineering, substantial ongoing writing. This complements trials by giving players a reason to care about the outcome beyond a medal.

## 8. Avenue F: asynchronous hold campaigns

**Purpose:** let veterans contribute to a changing shared situation while preserving casual participation.

**Loop.** A hold faces a three-stage incident: find the cause, weaken the threat, then confront it. Players choose a contribution during their own short session. Scouting reveals a route; negotiation secures supplies; combat suppresses reinforcements. Finishing a stage changes the next board and available objectives.

For example, a necromancer's siege can end with a well-supplied defence or a dangerous assault through a discovered tunnel. This extends the current hunts' support roles into changing campaign state; simply adding another pool of HP would add little.

**Progression.** Keep personal participation seals, chapter records, and collective trophies. Scale goals from a recent active-player window and freeze them when a campaign starts. Cap credited contributions per account per day, while allowing ordinary play to continue. Support and combat should both qualify for meaningful personal progress. Quiet-server fallback stages can resolve to a different ending without making previously earned rewards vanish.

No simultaneous party, attendance schedule, player-to-player trading, or party leader is required. One veteran cannot buy campaign completion by spending an old septim hoard; resource donations, if included, have a small fixed contribution ceiling. A weekly spotlight may rotate, but an unfinished active battle must retain its frozen rules and resolution policy.

**Integration.** Use a separate proposed `skyrim_campaigns.json` with campaign ID, phases, bounded contribution ledger, and immutable reward receipts. Reuse the reward-mailbox pattern in `progression.py`, not merely `world_boss()`'s presentation. Serialise shared-state updates through a single commit path and add retryable profile-side reward delivery. The Notice Board shows one campaign card.

**Scope:** large. It touches shared-state consistency, participation balance, and writing. Defer until solo challenges establish demand and action recovery is in place.

## 9. Avenue G: targeted discoveries and a trophy hall

**Purpose:** turn late collection progress into purposeful exploration and give excess resources an attractive destination.

**Loop.** Select an exhibition or research project. A housecarl expedition returns a lead; the player then visits a guaranteed quest route and makes the discovery themselves. Different projects draw on different locations and skills. Display completed pieces in the homestead gallery alongside existing Wonders and Chronicle pages.

**Progression.** New research collections use finite, visible steps. Original Wonders remain rare trophies: the current code explicitly treats them as a long-term random hunt. Do not quietly convert them into guaranteed purchases. Add useful source hints to the existing collection panel, and make exhibitions possible without owning every Wonder.

Freeze the original collection as a named volume and put expansion collections in subsequent volumes. Otherwise adding content continually lowers a veteran's displayed completion percentage and moves the 100% badge target. Preserve already-awarded badges and explicitly decide which volume each future badge measures.

Decorative rooms, displays, and commissioned banners provide septim/material sinks. Use a small number of priced projects, not endlessly escalating construction levels. Cosmetic projects must never gate combat access.

**Integration.** Extend `collection_summary()`, the collection panel, expedition results, and homestead presentation. Add a proposed research catalogue and progress map; keep exhibition placement separate from item ownership. Change collection badge predicates deliberately in `badges.py`, with stable IDs for existing rewards.

**Scope:** small to medium. This is a useful parallel content track, but collectibles alone will not address the need for new decisions.

## 10. Avenue H: a fairer proving circuit

**Purpose:** give competitive players a recurring target that is less dependent on account age and number of attempts.

**Loop.** Feature an authored trial or short Pit gauntlet each week. Offer a standardised temporary combat sheet and a fixed set of loadout choices. Players have three scored attempts usable at any time during the week; the best result counts. Practice uses separate seeds and awards nothing persistent.

**Progression.** Rank by a published tuple: objective completion first, then explicit decision-based objectives, then a small efficiency tiebreaker. Equal results may share a place. Avoid real-time speed scoring on Discord. Characters retain their normal power in ordinary adventures; standardisation applies only inside the circuit.

Store medals and personal bests permanently. Weekly results archive at rollover. Keep the existing unrestricted Soul Cairn depth record; a capped-floor seasonal Cairn challenge can be a separate circuit format, not a replacement that erases veterans' records. Do not introduce winner-only combat power or repeatable UKPence rewards.

**Integration.** Reuse trial layouts and existing rankings/Pit UI, but add a separate scored-mode sheet, attempt ledger, score version, and result store. The current daily dungeon shares rooms but uses each player's own rolls; it is not a ready-made fair replay system. Introduce a run-local, versioned random stream with committed outcomes so equivalent actions see equivalent rolls and restarts cannot reroll them. Keep practice from advancing skills, quests, Wonders, daily tasks, or economy badges.

**Scope:** large if built first; medium after trials and robust run persistence. Randomness still affects outcomes, so describe the board as a constrained competition rather than a perfect measurement of skill.

## 11. Architecture and persistence

The current separation is a good fit. `data.py` holds definitions; `combat.py` contains small rules; `engine.py` owns state transitions; `progression.py` handles persistent progression; `sessions.py` stages launches; `views.py` renders and routes interactions. New modules above are proposed boundaries, not files that already exist. Avoid expanding the roughly 4,600-line engine with every new rule.

A compact proposed account shape:

```json
{
  "endgame_schema": 1,
  "chronicle": {"chapters": [], "proofs": [], "boons": [], "active_chapter": null},
  "trials": {"best": {}, "completed": []},
  "relics": [],
  "selected_relic": null,
  "quest_journal": {},
  "research": {},
  "collection_volumes": {},
  "endgame_receipts": []
}
```

Add fields only when their feature ships. Migrations should be idempotent and accept old records. Use stable IDs rather than display names. Distinguish permanent discoveries from per-character faction standing and temporary run effects. Explicitly test retirement preservation rather than relying on fields being accidentally omitted from the reset function.

**Protect complete actions.** JSON writes already use a temporary file, `fsync`, and replacement. That prevents partial-file contents; it does not make updates across profile, board, and campaign files transactional. Adventure launches have a profile-side recovery journal, but normal turns currently save the profile and then the board separately. A crash between those writes can leave progress and encounter state out of step. [JSON writer](/Users/ogme01/Documents/Projects/HMS-Victory/lib/core/file_operations.py:8), [launch journal](/Users/ogme01/Documents/Projects/HMS-Victory/lib/features/skyrim/sessions.py:115), [ordinary action saves](/Users/ogme01/Documents/Projects/HMS-Victory/lib/features/skyrim/views.py:594)

For new rewarding actions, store the resulting character change, board snapshot, action revision, and reward identifiers together at a recoverable commit point. Reject stale revisions. Replaying recovery must reconstruct the already-decided outcome, never roll again. Stage proofs with that same action so a failure cannot mint an achievement twice.

Retain the existing `delve_id`; one already exists independently of the Discord message ID. Storage and active-profile references are still message-keyed. Add a run index only if replacing/recovering boards or additional clients require it—there is no need to invent a second adventure identity for the first release.

For cross-profile rewards, keep immutable receipts and save receipt acknowledgement with the recipient's credited balance. The hunt reward code already demonstrates this pattern. It solves reward redelivery, but shared contribution updates still need their own serialised commit path. [Receipt implementation](/Users/ogme01/Documents/Projects/HMS-Victory/lib/features/skyrim/progression.py:60)

**Keep JSON deliberately bounded.** Archive completed weekly boards and run histories; retain compact bests, unique proofs, and unpaid receipts in live records. Do not discard delivered-receipt evidence until a durable archive/compaction boundary prevents old rewards being replayed. Single-process synchronous read/modify/write sections can remain viable at the present architectural scale. If measurements show growing write latency or the bot gains multiple writers, move the affected stores to SQLite transactions before expanding shared modes further. A database rewrite is not a prerequisite for the initial solo pack.

**Freeze rules for active runs.** Persist the content/rules version, layout, choices offered, loadout, and resolved random outcomes. Updating a trial or rotating a week must not change a half-finished adventure. Keep compatible old definitions until their active runs finish, or provide an explicit safe settlement path.

**Preserve the economy boundary.** Expansion rewards stay in septims, ingredients, materials, and cosmetic ownership. Only deliberately authored, one-time badges may use the existing UKPence bridge. Seasonal placement, repeated chapters, practice, or repeated resets must not generate recurring server-money payouts.

## 12. Discord presentation

Keep one primary objective on the hub, routed by `next_goal()`. New challenge variants use the existing Adventure offers, and further Hall progress appears in the current Hall panel. Preserve the existing navigation and entry click count. Do not append root-menu buttons, persistent combat controls, or additional inventory panels. The parked systems are outside this interface budget and require a new scope decision before consideration.

The main board should show scene, concise state, one actionable rule, then actions. For example:

```text
RUNE WARDEN · Room 4/6
♥ 3/4 · Enemy 2 HP · 1 potion
Its ward resists Blade. Bow or Fire will break it.

[Blade] [Bow] [Fire]
[Guard] [Heal]
[Shout ▾]
[Leave] [Inspect] [Home]
```

The actual renderer should retain current hit-chance labels. A story room uses two or three contextual choices in place of combat controls. Preview entry costs, restrictions, and failure consequences in the existing adventure offer. Put encounter details behind Inspect. The example demonstrates existing controls handling a new enemy; it does not propose adding all those controls to rooms that currently need fewer. New players see no locked endgame buttons or extra explanatory panels.

The checkout already limits groups to three short buttons and has component/readability tests. Discord's current V2 limit is 40 total components; an action row holds up to five buttons or one select. The brief's five-row limit describes legacy messages. Treat five rows as a useful design budget for new screens, while counting every nested component and preserving the existing narrower button layout. [Discord component reference](https://docs.discord.com/developers/components/reference), [row layout](/Users/ogme01/Documents/Projects/HMS-Victory/lib/features/skyrim/views.py:296), [readability checks](/Users/ogme01/Documents/Projects/HMS-Victory/tests/test_skyrim_readability.py:60)

## 13. Delivery order and validation

| Release | Concrete scope | Evidence needed before expanding |
| --- | --- | --- |
| First playable pack | Repeatable retirement beyond Rank 5 with bounded power and access requirements; action recovery for new rewarding actions; one automatic Hall chapter with the first extra passive boon; three adventure variants; existing next-goal routing. | Existing Rank 5 players can qualify and reset; later lives can qualify again; old profiles and boards survive; boon effects are balanced and awarded once; no added hub/combat controls or entry steps. |
| Further content in the same interface | Two further boon milestones, reaching the three-extra-boon cap; one connected faction story; additional encounter patterns and routes based on the pilot. | Stories fit existing rooms and faction objectives; no extra management screens; choices stay readable on mobile. |
| Parked alternatives | Apocrypha drafting, relics, research/exhibitions, shared campaigns, and proving circuit. | Not scheduled. Reconsider only if there is a demonstrated need and the interaction can fit the simplicity constraint. |

Do not launch all eight avenues together. The first pack is valuable by itself and produces evidence for whether the playerbase prefers tactical challenges, story, collecting, or competition. **Three trials and one chapter are a pilot, not a months-long content supply:** dedicated players may finish their basic objectives quickly. Sustained lifespan depends on replayable run choices, personal records, and continued affordable additions. Expand successful mechanics through a reviewed catalogue of compatible trial variants; use weekly spotlights to surface that catalogue while leaving its permanent rewards obtainable. Measure how long one new trial or story costs to author before promising a release cadence.

Before implementing, take an authorised aggregate snapshot of current progression and activity to validate the brief. During a pilot, segment newcomers, post-Alduin players, Legacy 4, and Legacy 5. With a small server, raw counts and player comments are more informative than precise-looking retention percentages.

Track challenge starts/completions/abandonments, repeat attempts on a different option, decision counts, active play duration, reward totals, and return visits over one and four weeks. Record a few compact event fields; the current bounded in-memory game log is not a complete analytical history. Check whether veterans explore varied content and whether newcomers' progression changes. More clicks caused by repeated failure are not success.

Future implementation checks should include:

- Old and new profile migrations; retirement preservation; ongoing old-format adventure recovery. Cover existing Rank 5 characters, Rank 8+ characters with every new boon, successive qualifying lives, missing baseline evidence, and repeated reset confirmation. Verify that rank increases do not increase the capped Cairn resistance or replay badge payouts.
- Failed Discord posting without spent attempts; duplicate/stale clicks; crashes between each action-commit step; repeated reward delivery and week rollover.
- Seeded simulations for minimum eligible, midgame, and maxed builds, including Ward/Guard choices and pact/relic/gift incompatibilities. Compare reward per attempt and per active minute with existing modes.
- For any later practice or circuit proposal, isolation across XP, skills, inventory, tasks, collections, and badges.
- Compare old and new views for zero additional hub or persistent combat buttons and unchanged entry click counts. Check rendered mobile and desktop screens for combat, story choices, failure, and resumed boards.

The repository's [isolated Skyrim runner](/Users/ogme01/Documents/Projects/HMS-Victory/scripts/test_skyrim_isolated.py:1) supplies a starting point for that verification. No gameplay tests were run for this document-only analysis, and no implementation or deployment is claimed.

## 14. Choices to defer

Unbounded stacking of boon effects would undermine the current power curve. The chosen design permits an unlimited retirement count while capping combat effects and extra boon ownership separately. A new hard retirement stop at Rank 8 is outside this recommendation: collecting the extra boons must not end the reset loop again.

Likewise, avoid taller gear ladders, endless boss-health increases, mandatory daily streak rewards, permanent seasonal resets, live multiplayer turn coordination, and player trading in the first expansion. Each adds obligations or implementation cost without directly addressing the demonstrated lack of new goals.

**The first shipping decision should be repeatable retirement beyond Rank 5, capped extra boon rewards, and three new adventure variants, using the existing interface.** That gives a capped veteran a reason to return, lets the approaching cohort see what comes next, and tests how much depth can be added without increasing the game's management burden.
