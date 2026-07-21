# County Balls - art generation brief

Brief for generating the full county ball asset set. One image per row in the roster below, saved to the given output path (relative to the repo root).

## Style spec (applies to every image)

Match the look of the best community countryball fan art (BallsDex-style): **hand-drawn digital illustration with soft cel shading, warm highlights and painterly depth**. Not flat vector art, not clipart, not geometric renders.

- A single round ball character. Its body is the county flag or design described in the "Ball pattern" column, wrapped naturally around the sphere with shading that follows the curve.
- **Eyes carry all the emotion.** Large white eyes with bold black outlines, but expressive and varied: squinting with joy, closed and content, determined with angled brows, tired, smug, awed - whatever the row's "Character" column calls for. Eyebrows welcome. **No mouth** (countryball convention: expression lives entirely in the eyes).
- **Personality first.** Each row's "Character" column gives a mood, pose, props and easter eggs. The props should feel worn or held naturally (a hat sits with weight, a mug is nestled against the body), not floated beside a plain circle.
- Bold clean outlines, confident linework, slight variation in line weight.
- Fully transparent background. **No drop shadow, no ground contact shading** - shadows key into ragged semi-transparent blobs. The ball and its props must be the only opaque content. No scenery or backdrops.
- Absolutely no text, letters or numbers anywhere in the image.
- Keep the set consistent: same rendering style, same outline language, same eye anchoring - the variety comes from expression and props, not from art style drift.

## Process requirements (important)

- **Every asset must come from the image-generation model.** Never substitute a procedurally drawn image (PIL/matplotlib/SVG/code-rendered) for any county, under any circumstance - a missing file with a note in the audit is better than an off-style placeholder.
- If a generation attempt is refused or fails, rephrase the prompt and retry; if it still fails after a few attempts, skip it and list it in the final report.
- Generate on a plain pure-white background with no shadow, then remove the background by **flood fill from the image borders** (only the background region connected to the edge - never a global "remove all white", which would hollow out white eyes and white flag fields), followed by a ~1px defringe/erode so no white halo shows on Discord dark theme. Never use a green background: many county flags are green, and painterly edges pick up green spill.

## Technical requirements

- Render at 1024x1024, then downscale and save as **512x512 lossy WebP, quality ~80**, RGBA so transparency survives, at the exact output path in the table. Target under ~40KB per file (tiny disk on the host).
- File name must exactly match the path given (these are stable keys used by the bot).

## Flag accuracy

The "Ball pattern" column is a starting description. Where it says **(verify)**, check the registered county flag on the Flag Institute registry / Wikipedia before drawing and correct any detail I got wrong. Where it says **registered flag (research)**, look up the registered flag or, if the county has none, the historic county coat of arms, and use its dominant colours and main charge. Getting the flag right matters: guessing the county from the art is the whole game.

## Rarity

Tiers do not affect the art. Rarity follows popularity: the famous, populous places everyone wants are the rarest (London and Yorkshire are legendary), while small obscure counties are common.

## Roster

### England (40)

| Output path | County | Tier | Ball pattern | Character |
|---|---|---|---|---|
| data/counties/london.webp | London | legendary | City of London flag: white, red St George cross, red sword upright in the upper hoist canton (verify) | Beefeater hat worn with total confidence, smug half-lidded eyes, a gold pocket watch chain draped across the flag |
| data/counties/bedfordshire.webp | Bedfordshire | uncommon | Quartered gold and red, black band with three white scallop shells (verify) | straw boater hat (Luton hatmaking), easy content smile in the eyes |
| data/counties/berkshire.webp | Berkshire | uncommon | Yellow over green, white hart beneath an oak tree (verify) | one posh raised eyebrow, a small royal corgi companion wearing a tiny crown |
| data/counties/buckinghamshire.webp | Buckinghamshire | uncommon | Halved red over black, white swan wearing a chain (verify) | chin-up proud pose; easter egg: a giant peach resting beside it (Roald Dahl) |
| data/counties/cambridgeshire.webp | Cambridgeshire | uncommon | Blue, three gold crowns above wavy blue and white water (verify) | serene closed-eye calm, punting pole held upright, straw boater tipped back |
| data/counties/cheshire.webp | Cheshire | uncommon | Blue, three gold wheatsheaves around an upright gold sword (verify) | mischievous narrowed eyes; a floating Cheshire Cat grin hanging in the air beside it |
| data/counties/cornwall.webp | Cornwall | uncommon | Black with a centred white cross (St Piran's flag) | protectively clutching a pasty, wary sideways eyes at a menacing seagull looming in the corner |
| data/counties/cumberland.webp | Cumberland | common | registered flag (research) | drowsy content eyes, a coiled Cumberland sausage worn like a scarf |
| data/counties/derbyshire.webp | Derbyshire | uncommon | Green and blue quarters split by a white-edged cross, gold Tudor rose at centre (verify) | hiking bobble hat (Peak District), rosy cheeks, holding a Bakewell tart |
| data/counties/devon.webp | Devon | rare | Green, black cross edged white (St Petroc's flag) | serene superior closed eyes, presenting a scone with the cream spread FIRST, jam on top |
| data/counties/dorset.webp | Dorset | uncommon | Gold, white cross edged red (Dorset Cross) | relaxed beach-day eyes, sunhat, a spiral ammonite fossil held like treasure |
| data/counties/durham.webp | Durham | uncommon | Quartered blue and gold by a white cross, St Cuthbert's cross at centre (verify) | sturdy proud gaze, miner's lamp glowing warmly at its side |
| data/counties/essex.webp | Essex | rare | Red, three white seaxes (notched Saxon swords) stacked | white sunglasses pushed up on its head, confident smirking eyes, gold chain |
| data/counties/gloucestershire.webp | Gloucestershire | uncommon | registered flag (research; Severn Cross) | mid-tumble chasing a rolling cheese wheel, wildly determined eyes |
| data/counties/hampshire.webp | Hampshire | rare | Gold crown above a white and red rose (verify field colours) | sailor's cap, steady seafaring gaze, brass spyglass tucked at its side |
| data/counties/herefordshire.webp | Herefordshire | common | registered flag (research) | gentle sleepy eyes, a small white-faced Hereford calf dozing against it, cider apple nearby |
| data/counties/hertfordshire.webp | Hertfordshire | uncommon | registered flag (research; white hart) | commuter energy: takeaway coffee, rolled newspaper under an invisible arm, slightly tired but coping eyes |
| data/counties/huntingdonshire.webp | Huntingdonshire | common | Green, gold hunting horn (verify) | quietly proud; easter egg: wearing a black Cromwell-style hat |
| data/counties/kent.webp | Kent | rare | Red, white prancing horse (Invicta) | bright cheerful eyes, hop garland draped over it, a cricket ball at its base |
| data/counties/lancashire.webp | Lancashire | rare | Gold, one large red rose (verify) | flat cap, warm friendly eyes mid-glare-swivel: suspiciously eyeing a tiny distant white rose |
| data/counties/leicestershire.webp | Leicestershire | uncommon | registered flag (research; running fox and ermine cinquefoil) | sly knowing eyes, a red fox curled around its base; easter egg: a small gold crown half-buried under a parking cone |
| data/counties/lincolnshire.webp | Lincolnshire | uncommon | Quartered blue and green, red cross edged gold, gold fleur-de-lis at centre (verify) | wholesome big-sky smile in the eyes, holding a sausage; easter egg: three jet trails of red, white and blue smoke arcing overhead (Red Arrows) |
| data/counties/middlesex.webp | Middlesex | rare | Red, three white seaxes beneath a gold Saxon crown | prim pursed expression in the brows, bowler hat, furled umbrella hooked at its side |
| data/counties/norfolk.webp | Norfolk | uncommon | registered flag (research; gold with black and ermine bend) | unbothered half-closed eyes, perched on a tiny tractor, a turkey companion staring at the viewer |
| data/counties/northamptonshire.webp | Northamptonshire | uncommon | registered flag (research) | craftsman's proud eyes, polishing one immaculate leather boot with a cloth |
| data/counties/northumberland.webp | Northumberland | uncommon | Red and gold vertical zigzag stripes (pily) | stoic windswept squint, woolly hat, Northumbrian smallpipes tucked under an invisible arm, a few ancient wall stones at its feet |
| data/counties/nottinghamshire.webp | Nottinghamshire | uncommon | registered flag (research; cross and Robin Hood motif) | winking, Robin Hood hat with a pheasant feather, longbow slung across its back |
| data/counties/oxfordshire.webp | Oxfordshire | uncommon | registered flag (research; red ox crossing a wavy ford) | insufferably clever single raised eyebrow, mortarboard, a thick leather book |
| data/counties/rutland.webp | Rutland | common | Green, gold horseshoes and acorns (verify) | drawn noticeably smaller than every other ball but with the proudest chest-out defiant eyes, tiny gold crown |
| data/counties/shropshire.webp | Shropshire | common | Blue, three gold leopard faces (loggerheads) | calm rural smile in the eyes, leaning on a shepherd's crook; easter egg: a tiny iron bridge trinket at its base |
| data/counties/somerset.webp | Somerset | uncommon | Yellow, red dragon rampant | swaying happily with closed eyes, cider mug in a wellington boot, festival flower garland |
| data/counties/staffordshire.webp | Staffordshire | uncommon | Gold Stafford knot, red and gold field (verify) | eyes squeezed shut laughing as a Staffie puppy licks its face, a delicate teacup nearby (the Potteries) |
| data/counties/suffolk.webp | Suffolk | uncommon | registered flag (research; sun and crown of St Edmund) | calm painterly gaze, artist's palette and brush, a Suffolk Punch horse standing behind |
| data/counties/surrey.webp | Surrey | rare | Blue and gold chequerboard | smug suburban contentment, quilted gilet, golf club resting on its shoulder |
| data/counties/sussex.webp | Sussex | uncommon | Blue, six gold martlets (heraldic swallows) | breezy seaside eyes, striped deckchair folded beside it, martlets fluttering around its head |
| data/counties/warwickshire.webp | Warwickshire | uncommon | Bear holding a ragged staff (verify field colours) | theatrical soulful eyes, white ruff collar, quill in an invisible hand, a bear companion with a ragged staff |
| data/counties/westmorland.webp | Westmorland | common | registered flag (research; apple tree and bars) | cheerfully soggy: hiking boots, folded map, a tiny personal rain cloud drizzling on it |
| data/counties/wiltshire.webp | Wiltshire | uncommon | Green and white horizontal stripes, great bustard bird at centre (verify) | mysterious hooded-brow stare, standing among a few miniature trilithon stones, a great bustard perched on top |
| data/counties/worcestershire.webp | Worcestershire | uncommon | registered flag (research; black pears) | mildly apologetic eyes, a small unlabelled brown sauce bottle held out like an offering, a black pear |
| data/counties/yorkshire.webp | Yorkshire | legendary | Sky blue, one large white rose of York | supremely content closed eyes, flat cap, steaming mug of tea, a whippet curled at its base |

### Wales (13)

| Output path | County | Tier | Ball pattern | Character |
|---|---|---|---|---|
| data/counties/anglesey.webp | Anglesey | common | registered flag (research) | serene sea-breeze eyes, druid hood; easter egg: a comically long blank village signpost stretching out of frame |
| data/counties/brecknockshire.webp | Brecknockshire | common | registered flag (research) | windswept determined eyes, hiking backpack and map (the Beacons) |
| data/counties/caernarfonshire.webp | Caernarfonshire | common | Green, three gold eagles (verify) | mountain-proud gaze, slate-grey miner's helmet, an eagle perched on top |
| data/counties/cardiganshire.webp | Cardiganshire | common | registered flag (research) | cosy contented eyes: it is, of course, wearing a knitted cardigan |
| data/counties/carmarthenshire.webp | Carmarthenshire | uncommon | registered flag (research) | knowing twinkle in the eyes, a wizard's hat and long white beard (Merlin of Carmarthen) |
| data/counties/denbighshire.webp | Denbighshire | uncommon | registered flag (research) | calm hill-walker eyes, walking stick, small sheep companion |
| data/counties/flintshire.webp | Flintshire | uncommon | registered flag (research) | friendly open eyes, flat cap, holding a leek like a staff |
| data/counties/glamorgan.webp | Glamorgan | rare | Three white chevrons on red (verify) | mid-song: eyes closed in passionate hymn, daffodil tucked behind where an ear would be, rugby ball at its base |
| data/counties/merionethshire.webp | Merionethshire | common | registered flag (research; three white goats) | zen closed eyes, three white goats clambering over and around it |
| data/counties/monmouthshire.webp | Monmouthshire | uncommon | registered flag (research) | patient fisherman's eyes, rod dangling a line, wicker creel |
| data/counties/montgomeryshire.webp | Montgomeryshire | common | registered flag (research) | gentle eyes glancing up at a lamb asleep on top of its head |
| data/counties/pembrokeshire.webp | Pembrokeshire | uncommon | Blue over green, gold cross, Tudor rose at centre (verify) | delighted seaside eyes, bucket and spade, a puffin standing on its head |
| data/counties/radnorshire.webp | Radnorshire | common | registered flag (research) | quiet hermit-cosy smile in the eyes, a red kite circling above |

### Scotland (33)

| Output path | County | Tier | Ball pattern | Character |
|---|---|---|---|---|
| data/counties/aberdeenshire.webp | Aberdeenshire | uncommon | registered flag (research) | deadpan eyes peering through a shaggy Highland-cow fringe hanging over its face, hard hat on top |
| data/counties/angus.webp | Angus | common | registered flag (research) | serene farm eyes, a glossy black Angus bull standing protectively behind |
| data/counties/argyllshire.webp | Argyllshire | common | registered flag (research) | mildly tormented eyes swatting at a tiny cloud of midges, kilt sash |
| data/counties/ayrshire.webp | Ayrshire | uncommon | registered flag (research) | poetic faraway gaze, tam o' shanter, quill, a wee field mouse companion (Burns) |
| data/counties/banffshire.webp | Banffshire | common | registered flag (research) | patient fisher eyes, draped fishing net, one proud herring |
| data/counties/berwickshire.webp | Berwickshire | common | registered flag (research; bear and wych elm) | soft parental eyes, a bear cub dozing against it under an elm sapling |
| data/counties/buteshire.webp | Buteshire | common | registered flag (research) | blissful seaside snooze, closed eyes, knotted hankie sun hat, deckchair |
| data/counties/caithness.webp | Caithness | common | registered flag (research; galley and raven) | squinting hard into a horizontal gale, scarf streaming sideways, a raven gripping on for dear life |
| data/counties/clackmannanshire.webp | Clackmannanshire | common | registered flag (research) | drawn small like Rutland but with huge confident eyes: wee county, big energy |
| data/counties/dumfriesshire.webp | Dumfriesshire | common | registered flag (research) | gentle shepherd eyes, crook, a black-faced sheep companion |
| data/counties/dunbartonshire.webp | Dunbartonshire | common | registered flag (research) | industrious grin in the eyes, flat cap, welding goggles pushed up (Clydebank shipyards) |
| data/counties/east_lothian.webp | East Lothian | common | registered flag (research) | focused golfer eyes lining up a putt, club held ready, ball teetering on the edge of a sandy divot |
| data/counties/fife.webp | Fife | uncommon | registered flag (research) | pro-golfer calm, closed satisfied eyes, club resting on its shoulder, argyle-pattern bobble hat |
| data/counties/inverness_shire.webp | Inverness-shire | uncommon | registered flag (research) | delighted wide eyes as a small friendly Loch Ness monster peeks over its shoulder |
| data/counties/kincardineshire.webp | Kincardineshire | common | registered flag (research) | bracing sea-spray eyes, fishing creel on its back |
| data/counties/kinross_shire.webp | Kinross-shire | common | registered flag (research) | maximally relaxed closed eyes, fishing rod propped, line in an unseen loch |
| data/counties/kirkcudbrightshire.webp | Kirkcudbrightshire | common | registered flag (research) | dreamy artist eyes, beret, paintbrush held aloft mid-stroke |
| data/counties/lanarkshire.webp | Lanarkshire | rare | registered flag (research) | cheeky patter in the eyes, orange bobble hat, thumbs-up energy |
| data/counties/midlothian.webp | Midlothian | rare | registered flag (research) | dignified gaze, bagpipes under an invisible arm, a small loyal Skye terrier sitting at its base |
| data/counties/moray.webp | Moray | common | registered flag (research) | rosy blissful closed eyes leaning on a whisky barrel, a single hiccup bubble |
| data/counties/nairnshire.webp | Nairnshire | common | registered flag (research) | determinedly optimistic eyes, sunglasses and beach towel despite an implied grey sky |
| data/counties/orkney.webp | Orkney | common | Red, blue Nordic cross edged in gold (verify) | adventurous eyes, viking helmet, a puffin wearing a matching tiny helmet |
| data/counties/peeblesshire.webp | Peeblesshire | common | registered flag (research) | cosy hygge eyes, chunky wool scarf, knitting needles mid-row |
| data/counties/perthshire.webp | Perthshire | uncommon | registered flag (research) | peaceful eyes among drifting autumn leaves, tartan thermos |
| data/counties/renfrewshire.webp | Renfrewshire | uncommon | registered flag (research) | pleased-with-itself eyes, luxuriant paisley-pattern scarf (paisley comes from Paisley) |
| data/counties/ross_and_cromarty.webp | Ross and Cromarty | common | registered flag (research) | shifty side-glancing eyes, wearing a comically long two-tone scarf: two counties in one trench coat energy |
| data/counties/roxburghshire.webp | Roxburghshire | common | registered flag (research) | muddy and thrilled, rugby ball tucked in tight, scuffed happy eyes |
| data/counties/selkirkshire.webp | Selkirkshire | common | registered flag (research) | mild tweedy contentment, flat cap, a bannock balanced on an invisible knee |
| data/counties/shetland.webp | Shetland | common | Blue, white Nordic cross | cheerful eyes in horizontal sleet, Fair-Isle-pattern bobble hat, a Shetland pony pressed against it for warmth |
| data/counties/stirlingshire.webp | Stirlingshire | uncommon | registered flag (research) | fierce determined brows, a streak of blue face paint across the flag, claymore planted point-down |
| data/counties/sutherland.webp | Sutherland | common | registered flag (research) | stoic far-horizon gaze, windswept, one loyal sheep companion |
| data/counties/west_lothian.webp | West Lothian | common | registered flag (research) | friendly workaday eyes, flat cap, thermos of tea |
| data/counties/wigtownshire.webp | Wigtownshire | common | registered flag (research) | utterly absorbed reading eyes, half-moon glasses, teetering stack of books (Scotland's book town) |

### Northern Ireland (6)

| Output path | County | Tier | Ball pattern | Character |
|---|---|---|---|---|
| data/counties/antrim.webp | Antrim | rare | historic county arms (research), use dominant arms colours | awed upward gaze, standing on hexagonal basalt columns, one giant's boot resting nearby (Finn McCool) |
| data/counties/armagh.webp | Armagh | uncommon | historic county arms (research) | serene choir-boy eyes, a shiny red apple balanced perfectly on top (the Orchard County) |
| data/counties/down.webp | Down | uncommon | historic county arms (research) | happy hiker eyes, bobble hat, a tiny dry-stone wall at its feet (the Mournes) |
| data/counties/fermanagh.webp | Fermanagh | common | historic county arms (research) | misty-morning calm, closed eyes, fishing rod, sitting in a tiny rowing boat |
| data/counties/londonderry.webp | Londonderry | uncommon | historic county arms (research) | warm welcoming eyes, flat cap, a single oak leaf drifting down (Doire, the oak grove) |
| data/counties/tyrone.webp | Tyrone | uncommon | historic county arms (research) | match-day fever eyes, white and red headband, GAA ball held aloft |
