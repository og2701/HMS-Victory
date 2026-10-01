"""Rank card layout (``/rank``).

The card stays 1000x600, the size the purchasable backgrounds are drawn for. The
member's background fills a banner across the top, the seam beneath it is their
peerage progress bar (in their chosen colours), and the panel below carries the
avatar, name, title and badges.

``generate_rank_card`` in lib/core/utils.py gathers the member's data into a plain dict;
``build_rank_card_html`` here turns it into markup so the layout is testable offline.
"""

from __future__ import annotations

import html

from lib.core.file_operations import read_html_template

BADGE_BOX_W = 928          # lower panel width inside its padding
BADGE_BOX_H = 120          # from just under the avatar down to the bottom padding
BADGE_GAP = 6
RARITY_ORDER = {"Secret": -1, "Gold": 0, "Silver": 1, "Bronze": 2}


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def tier_progress(xp: int, thresholds) -> dict:
    """Progress through the current peerage, measured from its threshold (not from zero).

    thresholds: ascending [(xp_needed, role_id)].
    """
    current_id, floor = None, 0
    for needed, role_id in thresholds:
        if xp >= needed:
            current_id, floor = role_id, needed
        else:
            span = needed - floor
            return {"current_id": current_id, "next_id": role_id, "to_next": needed - xp,
                    "progress": (xp - floor) / span if span else 0.0}
    return {"current_id": current_id, "next_id": None, "to_next": None, "progress": 1.0}


def fit_badge_cell(count: int, box_w: int = BADGE_BOX_W, box_h: int = BADGE_BOX_H,
                   gap: int = BADGE_GAP, max_cell: int = 34, min_cell: int = 16) -> int:
    """Largest square badge size that fits every badge inside the box."""
    if count <= 0:
        return max_cell
    for cell in range(max_cell, min_cell - 1, -1):
        cols = max(1, (box_w + gap) // (cell + gap))
        rows = (count + cols - 1) // cols
        if rows * (cell + gap) - gap <= box_h:
            return cell
    return min_cell


def name_size(name: str) -> int:
    n = len(name)
    return 42 if n <= 12 else 36 if n <= 18 else 30 if n <= 24 else 26


def _badge(b: dict) -> str:
    img = f'<img src="{esc(b["src"])}" alt="{esc(b.get("name", ""))}">'
    rarity = (b.get("rarity") or "Bronze").lower()
    if rarity == "secret":
        return f'<div class="badge secret"><div>{img}</div></div>'
    return f'<div class="badge {rarity}">{img}</div>'


def build_rank_card_html(card: dict) -> str:
    """card keys: username, title, rank_label, xp, current_role, next_role, to_next, progress,
    ukpence, shutcoins (None hides it), ukpence_icon, shutcoin_icon, avatar_url, background,
    primary, secondary, tertiary, badges [{src, rarity, name}]."""
    badges = sorted(card.get("badges", []), key=lambda b: RARITY_ORDER.get(b.get("rarity"), 3))
    cell = fit_badge_cell(len(badges))

    coins = [f'<div class="chip coin"><img src="{esc(card["ukpence_icon"])}"><span class="num">{card["ukpence"]:,}</span></div>']
    if card.get("shutcoins") is not None:
        coins.append(f'<div class="chip coin"><img src="{esc(card["shutcoin_icon"])}"><span class="num">{card["shutcoins"]:,}</span></div>')

    current = card.get("current_role") or "No peerage yet"
    if card.get("next_role"):
        right = f'<div class="chip tier next">{card["to_next"]:,} XP to {esc(card["next_role"])}</div>'
    else:
        right = '<div class="chip tier max">Highest peerage reached</div>'
    progress = max(0.0, min(1.0, float(card.get("progress", 0))))

    title = card.get("title")
    title_html = f'<div class="title-chip ellipsis">{esc(title)}</div>' if title else ""
    noun = "badge" if len(badges) == 1 else "badges"

    body = (
        '<div class="card">'
        f'<div class="banner" style="background-image:linear-gradient(180deg, rgba(17,18,20,0) 50%, '
        f"rgba(17,18,20,0.65) 100%), url('{card['background']}')\">"
        f'<div class="banner-row"><div class="chip rank-chip num">{esc(card["rank_label"])}</div>'
        f'<div class="coins">{"".join(coins)}</div></div>'
        f'<div class="banner-row bottom"><div class="chip tier current">{esc(current)}</div>{right}</div>'
        '</div>'
        f'<div class="seam"><div style="width:{progress * 100:.1f}%"></div></div>'
        '<div class="lower">'
        '<div class="ident">'
        f'<div class="name-row"><div class="name ellipsis" style="font-size:{name_size(card["username"])}px">'
        f'{esc(card["username"])}</div>{title_html}</div>'
        f'<div class="subline">{card["xp"]:,} XP · {len(badges)} {noun}</div>'
        '</div>'
        f'<div class="badges">{"".join(_badge(b) for b in badges)}</div>'
        '</div>'
        f'<div class="avatar-ring"><img src="{esc(card["avatar_url"])}"></div>'
        '</div>'
    )

    template = read_html_template("templates/rank_card.html")
    for token, value in (
        ("{{primary}}", card["primary"]),
        ("{{secondary}}", card["secondary"]),
        ("{{tertiary}}", card["tertiary"]),
        ("{{cell}}", str(cell)),
    ):
        template = template.replace(token, esc(value))
    # Body last, so a member's name can never be read as a template token.
    return template.replace("{{body}}", body)
