# Skyrim scene art

Landscape scenes shown in the delve/hub MediaGallery (`lib/features/skyrim/views.py`,
`_asset_bytes`). Every scene falls back to text if its file is missing, so partial
drops are fine. Files are downscaled to 512px on load and cached.

Ship scenes as **WebP, max side 1024** (`.webp` is preferred over `.png` by the
loader) - the full-quality 1536px PNG masters from gpt-image-2 are ~3MB each,
which the VM's small disk can't afford; at 1024px WebP q85 they are ~150KB. To
convert a fresh drop:
`python -c "from PIL import Image; im=Image.open('x.png').convert('RGB'); im.thumbnail((1024,1024)); im.save('x.webp','WEBP',quality=85,method=6)"`

| file (.webp) | shown for |
|---|---|
| hub | the /skyrim hub panel |
| intro | first-run class pick ("you're finally awake") |
| victory | delve cleared |
| death | player death |
| leave | walked/fled out with the satchel |
| skeever, wolf, bandit, draugr, spider, necromancer, troll, hagraven, falmer | trash encounters |
| bandit_chief, deathlord, the_caller, centurion, dragon | bosses |
| chest, sweetroll, shrine, satchel, maiq, knee_trap, giant, wordwall | events |
| fork | the branching Fork event (safe way vs deep way) |
| fallen | a Fallen Adventurer's corpse (loot it or lay them to rest) |
| mimic | a Mimic chest mid-bite (an enemy; variant of `chest`) |
| soul_cairn | the endless Soul Cairn descent (post-Alduin endgame) |

| stray | the 🐾 stray-companion event - keep it AMBIGUOUS (species revealed on befriending) |
| pet_meeko, pet_vix, pet_pincer, pet_corvus | companion portraits (Companion panel shows the active one) |
| ebony_warrior | the Ebony Warrior legend hunt |
| karstaag | Karstaag, the frost-troll king legend hunt |
| vale_dragon | Voslaarum & Naaslaarum, the twin dragons (shared by both) |
| pit | The Pit arena panel (Windhelm bout results) |
| pit_snilf, pit_rolff, pit_adelaisa, pit_uzoga, pit_bero, pit_hama, pit_ulfberth, pit_widow, pit_yrsarald, pit_bear | Pit champion portraits (bout preview + result; arena scene until dropped) |
| pit_hjoromir, pit_sisters, pit_korst, pit_stone_guest, pit_master | Pit champions 11-15, beyond the Bear |
| hunt_marauder, hunt_pale_lady, hunt_legion, hunt_red_hand, hunt_grahl, hunt_otar | the Week's Hunt bosses (march boards + Notice Board) |
| hunt_greymaw, hunt_colossus, hunt_tide_mother, hunt_sky_shadow | Hunt bosses 7-10 (beast/construct/monster/dragon) |
| homestead_1, homestead_2, homestead_3 | the Holdings estate: fresh plot/hall → growing estate → Great Hall crowned |
| notice_board | the Notice Board panel (daily + tasks + hunt) |
| hall_of_legends | the Hall of Legends panel (Legacy Rebirth) |
| duel_circle | ghost-duel boards (falls back to `pit` until dropped) |
| duel_victory, duel_defeat | settled duels: the ghost scattered / the player counted out (fall back to `duel_circle`) |

The **Named Dragons of the Week** all reuse `dragon.webp` - no per-dragon art needed.
New expansion scenes fall back to text like every other, so they can be dropped in
whenever. Only `fork`, `fallen`, `mimic`, `soul_cairn` are new to generate.

**Dragon fight states (optional):** the delve view swaps the dragon picture by state -
`<art>_air` while it's airborne, `<art>_grounded` once the Voice slams it down - and
falls back to the base `<art>` if a variant is absent. Drop any of these to enable it:

| file (.webp) | shown for |
|---|---|
| dragon_air | a normal dragon still in the sky (blades/fire miss, use a bow) |
| dragon_grounded | a normal dragon shouted to the ground (every style bites) |
| alduin_air | Alduin airborne between reflights |
| alduin_grounded | Alduin grounded and open to attack |
