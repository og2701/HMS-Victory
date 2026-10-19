"""Where each iceberg entry is drawn, worked out fresh on every render.

An entry keeps the spot it was given when it was added, unless that spot now overlaps an
entry added before it (or pokes over its tier's lines once tilted), in which case it moves
to the nearest free spot in its tier. If some entry can't be fitted that way, the tier is
tried again placing its longest entries first, and only if that fails too is the tier
drawn a size smaller, and so on down to MIN_FONT_SIZE. Before this, a full tier dropped
new entries on top of old ones at random.

Entries are tilted, so overlaps are checked between the tilted shapes themselves rather
than their upright bounding boxes, which would keep two parallel tilted lines further
apart than they need to be.

Pure Pillow, so it can be tested without the bot.
"""

import math
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

FONT_PATH = "data/fluff.ttf"
FONT_SIZE = 14
MIN_FONT_SIZE = 10
GAP = 1            # clear pixels kept round each entry (so two entries end up 2px apart)
SHADOW = (2, 2)    # the drop shadow sits this far down and right of the text
EDGE = 2           # kept clear of each tier's lines
STEP = 2           # grid the search for a free spot walks
BUFFER = 20        # transparent margin round the text before it's tilted

LEVEL_BOUNDS = {
    1: ((4, 2), (404, 81)),
    2: ((4, 89), (404, 163)),
    3: ((4, 174), (404, 266)),
    4: ((7, 276), (402, 353)),
    5: ((5, 364), (404, 446)),
    6: ((4, 456), (402, 525)),
}


@lru_cache(maxsize=None)
def load_font(size: int):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except IOError:
        return ImageFont.load_default()


def _corners(font, text: str, rotation: int, pad: int = 0):
    """The drawn text's tilted outline relative to its (x, y), shadow included.

    Mirrors draw_entries: the text sits at (BUFFER, BUFFER) on a canvas that's turned
    counter-clockwise about its centre, and that centre lands back where it started."""
    left, top, right, bottom = font.getbbox(text)
    cx, cy = (right - left) / 2, (bottom - top) / 2
    x0, y0 = left - pad, top - pad
    x1, y1 = right + SHADOW[0] + pad, bottom + SHADOW[1] + pad
    a = math.radians(rotation)
    cos_a, sin_a = math.cos(a), math.sin(a)
    out = []
    for px, py in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        dx, dy = px - cx, py - cy
        out.append((cx + dx * cos_a + dy * sin_a, cy - dx * sin_a + dy * cos_a))
    return out


def _box_offsets(font, text: str, rotation: int):
    """Upright bounding box of the tilted text relative to its (x, y)."""
    pts = _corners(font, text, rotation)
    return (math.floor(min(p[0] for p in pts)), math.floor(min(p[1] for p in pts)),
            math.ceil(max(p[0] for p in pts)), math.ceil(max(p[1] for p in pts)))


def _shapes_overlap(a, b) -> bool:
    """Separating-axis test for two convex quads."""
    for quad in (a, b):
        for i in range(2):
            (x0, y0), (x1, y1) = quad[i], quad[i + 1]
            nx, ny = y0 - y1, x1 - x0
            pa = [x * nx + y * ny for x, y in a]
            pb = [x * nx + y * ny for x, y in b]
            if max(pa) <= min(pb) or max(pb) <= min(pa):
                return False
    return True


class _Shape:
    def __init__(self, rel, x, y):
        self.pts = [(px + x, py + y) for px, py in rel]
        self.box = (min(p[0] for p in self.pts), min(p[1] for p in self.pts),
                    max(p[0] for p in self.pts), max(p[1] for p in self.pts))

    def hits(self, other) -> bool:
        a, b = self.box, other.box
        if a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]:
            return False
        return _shapes_overlap(self.pts, other.pts)


def _place_tier(items, bounds, font, allow_overlap: bool = False):
    """{id: (x, y)} placing items in the given order, or None if they don't all fit."""
    (bx0, by0), (bx1, by1) = bounds
    bx0, by0, bx1, by1 = bx0 + EDGE, by0 + EDGE, bx1 - EDGE, by1 - EDGE
    placed, spots = [], {}
    for item in items:
        ox0, oy0, ox1, oy1 = _box_offsets(font, item["text"], item["rotation"])
        rel = _corners(font, item["text"], item["rotation"], pad=GAP)
        lo_x, hi_x = bx0 - ox0, bx1 - ox1
        lo_y, hi_y = by0 - oy0, by1 - oy1
        if lo_x > hi_x or lo_y > hi_y:
            if not allow_overlap:
                return None
            hi_x, hi_y = max(lo_x, hi_x), max(lo_y, hi_y)
        ax = min(max(item["x"], lo_x), hi_x)
        ay = min(max(item["y"], lo_y), hi_y)

        def free(x, y):
            shape = _Shape(rel, x, y)
            return None if any(shape.hits(p) for p in placed) else shape

        spot, shape = (ax, ay), free(ax, ay)
        if shape is None:
            nearest_first = sorted(
                ((x, y) for x in range(lo_x, hi_x + 1, STEP) for y in range(lo_y, hi_y + 1, STEP)),
                key=lambda p: ((p[0] - ax) ** 2 + (p[1] - ay) ** 2, p))
            for spot in nearest_first:
                shape = free(*spot)
                if shape is not None:
                    break
            if shape is None:
                if not allow_overlap:
                    return None
                spot, shape = (ax, ay), _Shape(rel, ax, ay)
        placed.append(shape)
        spots[item["id"]] = spot
    return spots


def _fit_tier(items, bounds):
    """(font size, {id: (x, y)}) for one tier: oldest first, then longest first, then smaller."""
    for size in range(FONT_SIZE, MIN_FONT_SIZE - 1, -1):
        font = load_font(size)
        longest_first = sorted(items, key=lambda e: (-font.getlength(e["text"]), e["id"]))
        for order in (items, longest_first):
            spots = _place_tier(order, bounds, font)
            if spots is not None:
                return size, spots
    return MIN_FONT_SIZE, _place_tier(items, bounds, load_font(MIN_FONT_SIZE), allow_overlap=True)


def lay_out(entries):
    """entries: dicts with id, text, level, x, y, color, rotation (x, y as stored).

    Returns the same dicts in id order, each with its drawn x, y and font size."""
    out = []
    tiers = {}
    for e in sorted(entries, key=lambda e: e["id"]):
        tiers.setdefault(e["level"], []).append(e)
    for level, items in tiers.items():
        bounds = LEVEL_BOUNDS.get(level)
        if bounds is None:
            out.extend({**e, "size": FONT_SIZE} for e in items)
            continue
        size, spots = _fit_tier(items, bounds)
        out.extend({**e, "x": spots[e["id"]][0], "y": spots[e["id"]][1], "size": size} for e in items)
    return sorted(out, key=lambda e: e["id"])


def draw_entries(img: Image.Image, entries) -> Image.Image:
    """Draw laid-out entries (from lay_out) onto the iceberg, each with a drop shadow."""
    for e in entries:
        font = load_font(e["size"])
        left, top, right, bottom = font.getbbox(e["text"])
        w = (right - left) + BUFFER * 2
        h = (bottom - top) + BUFFER * 2
        layers = []
        for fill in ("black", e["color"]):
            canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ImageDraw.Draw(canvas).text((BUFFER, BUFFER), e["text"], font=font, fill=fill)
            layers.append(canvas.rotate(e["rotation"], resample=Image.BICUBIC, expand=1))
        shadow, text = layers
        # Put the tilted canvas's centre back where the flat one's was.
        shift_x = (text.width - w) // 2 + BUFFER
        shift_y = (text.height - h) // 2 + BUFFER
        img.paste(shadow, (e["x"] - shift_x + SHADOW[0], e["y"] - shift_y + SHADOW[1]), shadow)
        img.paste(text, (e["x"] - shift_x, e["y"] - shift_y), text)
    return img
