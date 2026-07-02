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
