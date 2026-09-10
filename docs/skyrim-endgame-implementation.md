# Skyrim endgame implementation

Implemented locally on `codex/skyrim-endgame`, based on `5430ed1`. No production deployment, bot restart or live Discord messages. Verification uses disposable saves.

## Shipped scope

- Retirement continues indefinitely after Legend 5. Later retirements require level 32, one Alduin victory in the current life, and no active adventure or bout. The existing confirmation, inheritance and character reset remain. Boons, account records, Hall deeds, titles and story progress survive.
- The original eight-boon catalogue and first five retirement choices remain. Three additional passive boons unlock automatically through three ordered Hall chapters: Steady Hands (lockpicking), Silver Tongue (persuasion), Quiet Step (sneaking). Each rerolls the first failed check of its type once per adventure. No equip or claim controls.
- Nine Hall deeds record completions and decisions. They are Skyrim-only records with no new server badges or UKPence rewards. The original collection catalogue and five-rank server badge threshold remain unchanged.
- Rune Warden, Lost Caravan and Sealed Vault open at level 20 after an Alduin victory. One occupies an existing adventure-offer slot, with its rule visible before entry. Each spends the usual delve charge. Other roads remain available.
- Three faction stories, two chapters each, open at the highest faction rank. A first-stage choice changes the next adventure. Only successful clears advance the story. Finishing grants a title and a Hall deed. After retirement, rejoining and regaining rank resumes an unfinished story.
- No additional hub or persistent combat buttons. Contextual room decisions replace existing room controls. The Hall and faction panels show the objective; Inspect contains history and boon effects. New deed, boon and story rewards appear on the adventure result.

## Roads and Hall chapters

| Road | Decision | Completion and mastery deeds |
| --- | --- | --- |
| Rune Warden | The ward absorbs a repeated landed weapon; change weapons. | Defeat it; defeat it after landing all three weapon styles. |
| Lost Caravan | Rescue the scout or take supplies; the captain opens with a charge. | Rescue and clear; also Guard the charge. |
| Sealed Vault | Pick the lock, negotiate, or force entry. A failed quiet attempt permits waiting for a quiet opening with 40 fewer base clear septims, or fighting. | Recover the ledger without an alarm; do so through successful negotiation. |

Chapter 1 requires the three completion deeds and grants Steady Hands. Chapter 2 requires the three mastery deeds and grants Silver Tongue. Chapter 3 requires a retirement beyond five, Cairn depth 20 and a completed faction story, granting Quiet Step. Deeds can be earned before Legend 5; their boons awaken at five. Waiting after failed negotiation does not count as successful negotiation.

The faction stories are The Broken Seal (College), The Raider's Trail (Companions), and The Missing Names (Thieves Guild). Their titles are Keeper of the Seal, Shield of the Road, and Keeper of Names. Choices trade protection for reduced boss HP, with an alarm or charging foe where applicable. Consequences are previewed before choosing. These stories use the new roads and existing room controls.

## Save compatibility and limits

`endgame.py` holds the Hall and authored-room rules. Profiles store `hall` and `legacy.life`; boards store an `endgame` snapshot and `revision`. Per-run boons and consumed uses are frozen into the board. Unlocks affect future adventures.

Old rank-five saves recover current-life baselines only when the latest epitaph has valid counters and matches the retirement count. Otherwise, current lifetime totals become the baseline, preserving history while requiring a new victory. Rebirth saves fresh baselines with the reset. A captured rank rejects repeated retirement confirmations.

From rank five onward Alduin's dragon gate uses current-life counters: 5 + 3 per current-life Alduin win, capped at four echoes (17 dragons). Combat Echo remains capped at four. Soul Cairn resistance uses at most five effective ranks (60%); endless retirements do not grant endless combat power.

Actions resolve on copies. A profile-side journal commits the resulting character and board, then materialises the shared board file. Recovery replays saved outcomes, never random rolls. Death records and daily results are idempotent and retry failed writes. One previous board is retained without recursive history, so old controls can refresh to the current turn after a restart or failed Discord edit. Registration retains at most two revisions per message. The existing launch journal remains in use.

## Verification

- `scripts/test_skyrim_isolated.py`: **184 passing checks**: 106 historical plain checks and 78 unittest cases, including 26 new endgame cases.
- Coverage includes old-save migration, repeated retirement, preserved account progress, bounded scaling, unchanged badge predicates, actual boon-enabled actions, persistent boon usage, both branches of all faction stories, distinct Vault outcomes, commit failures, death/daily recovery, stale clicks, previous-control routing with Discord's actual view store, compact layouts and visible reward notices.
- `scripts/simulate_skyrim_endgame.py`: **4,000 seeded runs**, 200 per road/configuration, all terminating within the 200-action bound. See [results](skyrim-endgame-balance.json). Two builds use level-appropriate perk budgets and three initial potions. Boons are tested off and on, including a hypothetical early-character case to isolate their effect.
- Sampled veteran clears were 100% for the authored roads and 97.5–99% for comparison roads. New roads took approximately 6–12 actions, earning 41–63 septims/action versus 48–73 on comparison roads. These are short decision and story encounters, not a new hardest difficulty tier. The sampled policy heals, Guards charges, changes Warden weapons and follows quiet Vault routes. This is not player telemetry or a guaranteed clear.
- **30 real component payloads at 360px and 780px**: 60 layouts, zero overflowing control rows and no captured browser console errors. The faction objective was rechecked after correcting summary truncation. The preferred browser CLI was unavailable; checks used the connected browser. Rendering approximates Discord; live Discord acceptance remains untested.
- Three scenes generated with the built-in image tool ship as 1152×768 WebP, 138–164 KB each. Originals remain intact. See [prompts and assets](skyrim-endgame-art.md).
- `git diff --check` passes. Unrelated database WAL/SHM files remain untouched.

Run test, simulation and preview scripts with `/Users/ogme01/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3`; system Python lacks Pillow. `scripts/preview_skyrim.py` refreshes the existing local component preview. Deployment is a separate action using the required repository deployment script.
