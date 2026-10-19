from lib.features.iceberg_layout import (
    FONT_SIZE, LEVEL_BOUNDS, MIN_FONT_SIZE, _box_offsets, _corners, _Shape, lay_out, load_font,
)

# Tier 3 as it stood when it filled up: the Ponzi scheme was dropped on top of the
# first entry because no free spot was left at the old fixed size.
TIER_3 = [
    (1, "Discord wanting all the money", 25, 181, -1),
    (2, "peter pan is cool cishet and ultra masc", 122, 193, 1),
    (3, "Abdullahsimpsonsfan", 30, 222, -2),
    (4, "Joe gets a brick of Fred on tick", 194, 222, 0),
    (5, "UKP Lesbian Polycule", 60, 248, -2),
    (6, "DPRUK", 220, 245, 0),
    (7, "Ukpence Ponzi scheme", 62, 186, -3),
]


def entries(rows, level=3):
    return [{"id": i, "text": t, "level": level, "x": x, "y": y, "color": "#ffcc00", "rotation": r}
            for i, t, x, y, r in rows]


def assert_tidy(laid, level):
    """Every entry inside its tier, and no two drawn entries (shadows included) touching."""
    (bx0, by0), (bx1, by1) = LEVEL_BOUNDS[level]
    shapes = []
    for e in laid:
        font = load_font(e["size"])
        ox0, oy0, ox1, oy1 = _box_offsets(font, e["text"], e["rotation"])
        assert bx0 <= e["x"] + ox0 and e["x"] + ox1 <= bx1, e["text"]
        assert by0 <= e["y"] + oy0 and e["y"] + oy1 <= by1, e["text"]
        shapes.append((e["text"], _Shape(_corners(font, e["text"], e["rotation"]), e["x"], e["y"])))
    for i, (ta, a) in enumerate(shapes):
        for tb, b in shapes[i + 1:]:
            assert not a.hits(b), (ta, tb)


def test_full_tier_is_laid_out_without_overlaps():
    assert_tidy(lay_out(entries(TIER_3)), 3)


def test_an_entry_on_a_free_spot_stays_put():
    laid = {e["id"]: e for e in lay_out(entries(TIER_3))}
    assert (laid[4]["x"], laid[4]["y"]) == (194, 222)
    assert (laid[6]["x"], laid[6]["y"]) == (220, 245)


def test_the_later_of_two_clashing_entries_is_the_one_that_moves():
    laid = {e["id"]: e for e in lay_out(entries(TIER_3))}
    assert (laid[1]["x"], laid[1]["y"]) == (25, 181)
    assert (laid[7]["x"], laid[7]["y"]) != (62, 186)


def test_an_overfull_tier_shrinks_its_text_until_it_fits():
    rows = [(i, f"iceberg entry number {i}", 10, 180, (i % 5) - 2) for i in range(1, 11)]
    laid = lay_out(entries(rows))
    assert MIN_FONT_SIZE <= laid[0]["size"] < FONT_SIZE
    assert len({e["size"] for e in laid}) == 1
    assert_tidy(laid, 3)


def test_other_tiers_are_left_alone():
    rows = entries(TIER_3) + entries([(8, "Worm maxing", 150, 30, 2)], level=1)
    laid = {e["id"]: e for e in lay_out(rows)}
    assert (laid[8]["x"], laid[8]["y"], laid[8]["size"]) == (150, 30, FONT_SIZE)


def test_layout_is_the_same_every_render():
    assert lay_out(entries(TIER_3)) == lay_out(entries(TIER_3))
