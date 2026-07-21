# County Balls - art generation brief

Brief for generating the full county ball asset set. Feed this file to codex; it should generate one image per row in the roster table below and save it to the given output path (relative to the repo root).

## Global style spec (applies to every image)

A countryball-style cartoon character: a single round ball with large simple white oval eyes outlined in black. No mouth, no arms, no legs. The ball's body is fully patterned as the county flag or design described in the "Ball pattern" column, wrapped naturally around the sphere. Flat cartoon colours, bold black outlines, minimal soft shading. Character centred, filling most of the frame. Fully transparent background. Absolutely no text, letters, or numbers anywhere in the image.

If the row's "Extras" column lists an accessory or companion, include it small and secondary to the ball. Keep the whole set stylistically consistent: same eye style, same outline weight, same proportions.

## Technical requirements

- Render at 1024x1024, then downscale and save as **512x512 lossy WebP, quality ~80** at the exact output path in the table. Target under ~40KB per file (tiny disk on the host).
- Transparent background must survive the WebP conversion (use `-alpha_q 100` / RGBA).
- File name must exactly match the path given (these are stable keys used by the bot).

## Flag accuracy

The "Ball pattern" column is a starting description. Where it says **(verify)**, check the registered county flag on the Flag Institute registry / Wikipedia before drawing and correct any detail I got wrong. Where it says **registered flag (research)**, look up the registered flag or, if the county has none, the historic county coat of arms, and use its dominant colours and main charge. Getting the flag right matters: guessing the county from the art is the whole game.

## Rarity

Tiers do not affect the art. Rarity follows popularity: the famous, populous places everyone wants are the rarest (London and Yorkshire are legendary), while small obscure counties are common. Spawn weights and sell prices come from the tier in the bot's roster data.

## Roster

### England (40)

| Output path | County | Tier | Ball pattern | Extras |
|---|---|---|---|---|
| data/counties/london.webp | London | legendary | City of London flag: white, red St George cross, red sword upright in the upper hoist canton (verify) | a Beefeater hat |
| data/counties/bedfordshire.webp | Bedfordshire | uncommon | Quartered gold and red, black band with three white scallop shells (verify) | none |
| data/counties/berkshire.webp | Berkshire | uncommon | Yellow over green, white hart beneath an oak tree (verify) | a small royal corgi companion |
| data/counties/buckinghamshire.webp | Buckinghamshire | uncommon | Halved red over black, white swan wearing a chain (verify) | none |
| data/counties/cambridgeshire.webp | Cambridgeshire | uncommon | Blue, three gold crowns above wavy blue and white water (verify) | holding a punting pole |
| data/counties/cheshire.webp | Cheshire | uncommon | Blue, three gold wheatsheaves around an upright gold sword (verify) | a grinning Cheshire cat companion |
| data/counties/cornwall.webp | Cornwall | uncommon | Black with a centred white cross (St Piran's flag) | holding a pasty |
| data/counties/cumberland.webp | Cumberland | common | registered flag (research) | a coiled Cumberland sausage |
| data/counties/derbyshire.webp | Derbyshire | uncommon | Green and blue quarters split by a white-edged cross, gold Tudor rose at centre (verify) | a Bakewell tart |
| data/counties/devon.webp | Devon | rare | Green, black cross edged white (St Petroc's flag) | a cream tea scone, cream spread first |
| data/counties/dorset.webp | Dorset | uncommon | Gold, white cross edged red (Dorset Cross) | a spiral ammonite fossil |
| data/counties/durham.webp | Durham | uncommon | Quartered blue and gold by a white cross, St Cuthbert's cross at centre (verify) | a miner's lamp |
| data/counties/essex.webp | Essex | rare | Red, three white seaxes (notched Saxon swords) stacked | wearing sunglasses |
| data/counties/gloucestershire.webp | Gloucestershire | uncommon | registered flag (research; Severn Cross) | a rolling cheese wheel |
| data/counties/hampshire.webp | Hampshire | rare | Gold crown above a white and red rose (verify field colours) | a sailor's hat |
| data/counties/herefordshire.webp | Herefordshire | common | registered flag (research) | a small white-faced Hereford calf companion |
| data/counties/hertfordshire.webp | Hertfordshire | uncommon | registered flag (research; white hart) | none |
| data/counties/huntingdonshire.webp | Huntingdonshire | common | Green, gold hunting horn (verify) | none |
| data/counties/kent.webp | Kent | rare | Red, white prancing horse (Invicta) | a sprig of hops |
| data/counties/lancashire.webp | Lancashire | rare | Gold, one large red rose (verify) | a hotpot bowl |
| data/counties/leicestershire.webp | Leicestershire | uncommon | registered flag (research; running fox and ermine cinquefoil) | none |
| data/counties/lincolnshire.webp | Lincolnshire | uncommon | Quartered blue and green, red cross edged gold, gold fleur-de-lis at centre (verify) | a sausage |
| data/counties/middlesex.webp | Middlesex | rare | Red, three white seaxes beneath a gold Saxon crown | bowler hat and umbrella |
| data/counties/norfolk.webp | Norfolk | uncommon | registered flag (research; gold with black and ermine bend) | perched on a tiny tractor |
| data/counties/northamptonshire.webp | Northamptonshire | uncommon | registered flag (research) | a leather boot |
| data/counties/northumberland.webp | Northumberland | uncommon | Red and gold vertical zigzag stripes (pily) | none |
| data/counties/nottinghamshire.webp | Nottinghamshire | uncommon | registered flag (research; cross and Robin Hood motif) | Robin Hood hat with a feather |
| data/counties/oxfordshire.webp | Oxfordshire | uncommon | registered flag (research; red ox crossing a wavy ford) | an academic mortarboard cap |
| data/counties/rutland.webp | Rutland | common | Green, gold horseshoes and acorns (verify) | a tiny gold crown |
| data/counties/shropshire.webp | Shropshire | common | Blue, three gold leopard faces (loggerheads) | none |
| data/counties/somerset.webp | Somerset | uncommon | Yellow, red dragon rampant | a cider mug |
| data/counties/staffordshire.webp | Staffordshire | uncommon | Gold Stafford knot, red and gold field (verify) | a Staffordshire bull terrier puppy |
| data/counties/suffolk.webp | Suffolk | uncommon | registered flag (research; sun and crown of St Edmund) | none |
| data/counties/surrey.webp | Surrey | rare | Blue and gold chequerboard | a tiny golf club |
| data/counties/sussex.webp | Sussex | uncommon | Blue, six gold martlets (heraldic swallows) | none |
| data/counties/warwickshire.webp | Warwickshire | uncommon | Bear holding a ragged staff (verify field colours) | none |
| data/counties/westmorland.webp | Westmorland | common | registered flag (research; apple tree and bars) | hiking boots |
| data/counties/wiltshire.webp | Wiltshire | uncommon | Green and white horizontal stripes, great bustard bird at centre (verify) | a small stone trilithon |
| data/counties/worcestershire.webp | Worcestershire | uncommon | registered flag (research; black pears) | a small brown sauce bottle, no label text |
| data/counties/yorkshire.webp | Yorkshire | legendary | Sky blue, one large white rose of York | flat cap and a mug of tea |

### Wales (13)

| Output path | County | Tier | Ball pattern | Extras |
|---|---|---|---|---|
| data/counties/anglesey.webp | Anglesey | common | registered flag (research) | none |
| data/counties/brecknockshire.webp | Brecknockshire | common | registered flag (research) | none |
| data/counties/caernarfonshire.webp | Caernarfonshire | common | Green, three gold eagles (verify) | none |
| data/counties/cardiganshire.webp | Cardiganshire | common | registered flag (research) | none |
| data/counties/carmarthenshire.webp | Carmarthenshire | uncommon | registered flag (research) | none |
| data/counties/denbighshire.webp | Denbighshire | uncommon | registered flag (research) | none |
| data/counties/flintshire.webp | Flintshire | uncommon | registered flag (research) | none |
| data/counties/glamorgan.webp | Glamorgan | rare | Three white chevrons on red (verify) | a daffodil |
| data/counties/merionethshire.webp | Merionethshire | common | registered flag (research; three white goats) | none |
| data/counties/monmouthshire.webp | Monmouthshire | uncommon | registered flag (research) | none |
| data/counties/montgomeryshire.webp | Montgomeryshire | common | registered flag (research) | none |
| data/counties/pembrokeshire.webp | Pembrokeshire | uncommon | Blue over green, gold cross, Tudor rose at centre (verify) | none |
| data/counties/radnorshire.webp | Radnorshire | common | registered flag (research) | a small red kite bird overhead |

### Scotland (33)

| Output path | County | Tier | Ball pattern | Extras |
|---|---|---|---|---|
| data/counties/aberdeenshire.webp | Aberdeenshire | uncommon | registered flag (research) | an oil-rig hard hat |
| data/counties/angus.webp | Angus | common | registered flag (research) | none |
| data/counties/argyllshire.webp | Argyllshire | common | registered flag (research) | none |
| data/counties/ayrshire.webp | Ayrshire | uncommon | registered flag (research) | a tam o' shanter hat |
| data/counties/banffshire.webp | Banffshire | common | registered flag (research) | none |
| data/counties/berwickshire.webp | Berwickshire | common | registered flag (research; bear and wych elm) | none |
| data/counties/buteshire.webp | Buteshire | common | registered flag (research) | none |
| data/counties/caithness.webp | Caithness | common | registered flag (research; galley and raven) | none |
| data/counties/clackmannanshire.webp | Clackmannanshire | common | registered flag (research) | none |
| data/counties/dumfriesshire.webp | Dumfriesshire | common | registered flag (research) | none |
| data/counties/dunbartonshire.webp | Dunbartonshire | common | registered flag (research) | none |
| data/counties/east_lothian.webp | East Lothian | common | registered flag (research) | none |
| data/counties/fife.webp | Fife | uncommon | registered flag (research) | a golf club and ball |
| data/counties/inverness_shire.webp | Inverness-shire | uncommon | registered flag (research) | a tiny friendly Loch Ness monster companion |
| data/counties/kincardineshire.webp | Kincardineshire | common | registered flag (research) | none |
| data/counties/kinross_shire.webp | Kinross-shire | common | registered flag (research) | none |
| data/counties/kirkcudbrightshire.webp | Kirkcudbrightshire | common | registered flag (research) | none |
| data/counties/lanarkshire.webp | Lanarkshire | rare | registered flag (research) | none |
| data/counties/midlothian.webp | Midlothian | rare | registered flag (research) | none |
| data/counties/moray.webp | Moray | common | registered flag (research) | a small whisky barrel |
| data/counties/nairnshire.webp | Nairnshire | common | registered flag (research) | none |
| data/counties/orkney.webp | Orkney | common | Red, blue Nordic cross edged in gold (verify) | a viking helmet |
| data/counties/peeblesshire.webp | Peeblesshire | common | registered flag (research) | none |
| data/counties/perthshire.webp | Perthshire | uncommon | registered flag (research) | none |
| data/counties/renfrewshire.webp | Renfrewshire | uncommon | registered flag (research) | none |
| data/counties/ross_and_cromarty.webp | Ross and Cromarty | common | registered flag (research) | none |
| data/counties/roxburghshire.webp | Roxburghshire | common | registered flag (research) | none |
| data/counties/selkirkshire.webp | Selkirkshire | common | registered flag (research) | none |
| data/counties/shetland.webp | Shetland | common | Blue, white Nordic cross | a tiny Shetland pony companion |
| data/counties/stirlingshire.webp | Stirlingshire | uncommon | registered flag (research) | none |
| data/counties/sutherland.webp | Sutherland | common | registered flag (research) | none |
| data/counties/west_lothian.webp | West Lothian | common | registered flag (research) | none |
| data/counties/wigtownshire.webp | Wigtownshire | common | registered flag (research) | a small stack of books |

### Northern Ireland (6)

| Output path | County | Tier | Ball pattern | Extras |
|---|---|---|---|---|
| data/counties/antrim.webp | Antrim | rare | historic county arms (research), use dominant arms colours | standing on hexagonal basalt columns |
| data/counties/armagh.webp | Armagh | uncommon | historic county arms (research) | a red apple |
| data/counties/down.webp | Down | uncommon | historic county arms (research) | none |
| data/counties/fermanagh.webp | Fermanagh | common | historic county arms (research) | a fishing rod |
| data/counties/londonderry.webp | Londonderry | uncommon | historic county arms (research) | none |
| data/counties/tyrone.webp | Tyrone | uncommon | historic county arms (research) | none |
