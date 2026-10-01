"""Roulette table images on the shared casino felt.

While bets are open the board is the real betting layout (set vertically, as seen from the
end of the table) with every player's chips on the spots they backed, in that player's
colour. Once the ball lands the same layout lights the winning number and bets, the chips
that won glow and the rest fade, and a small wheel in the rail shows where the ball sits.

Rules (wheel order, colours, what wins) live in commands/economy/roulette.py and are read
lazily, since that module imports this one.
"""

from __future__ import annotations

import math

from lib.economy import casino_felt as felt

CHIP_COLOURS = ["#3B82F6", "#F5B83D", "#A855F7", "#2DD4BF", "#FB923C", "#F472B6"]
CELL_W, ROW_H = 112, 50              # one number cell
GRID_X, GRID_Y = 196, 74             # top-left of the number grid inside the layout svg
ZERO_H = 64
GOLD = "#FFD95E"
FILL = {"red": "#B3242F", "black": "#15171A", "green": "#13804A"}


def _rules():
    from commands.economy import roulette
    return roulette


def chip_text(amount: int) -> str:
    """500 / 1K / 2.5K / 1M - short enough to sit on a chip."""
    if amount >= 1_000_000:
        return f"{amount / 1_000_000:g}M"
    if amount >= 1000:
        return f"{amount / 1000:g}K"
    return str(amount)


def player_colours(table) -> dict:
    """Chip colour per player, by the order they sat down (stable for the whole round)."""
    return {pid: CHIP_COLOURS[i % len(CHIP_COLOURS)] for i, pid in enumerate(table.players)}


# ---------------------------------------------------------------------------
# The betting layout
# ---------------------------------------------------------------------------
def layout_svg(table, result=None) -> str:
    R = _rules()
    width = GRID_X + 3 * CELL_W + 24
    height = GRID_Y + 13 * ROW_H + 30
    line = 'stroke="rgba(255,255,255,0.75)" stroke-width="2"'
    font = 'font-family="DM Serif Display"'
    out, spots = [], {}

    def wins(key):
        return result is not None and R.bet_wins(key, result)

    def cell(x, y, w, h, text, *, fill="none", size=26, ink="#FFFFFF", key=None, rotate=False):
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}" {line}/>')
        if key and wins(key):
            out.append(f'<rect x="{x + 3}" y="{y + 3}" width="{w - 6}" height="{h - 6}" fill="none" stroke="{GOLD}" stroke-width="5"/>')
        cx, cy = x + w / 2, y + h / 2
        turn = f' transform="rotate(-90 {cx} {cy})"' if rotate else ""
        out.append(f'<text x="{cx}" y="{cy + size * 0.34:.0f}" text-anchor="middle" {font} font-size="{size}" fill="{ink}"{turn}>{text}</text>')
        if key:
            spots[key] = (cx, cy)

    # zero, across the top
    out.append(f'<path d="M{GRID_X} {GRID_Y} L{GRID_X + CELL_W * 1.5} {GRID_Y - ZERO_H + 14} L{GRID_X + 3 * CELL_W} {GRID_Y} Z" '
               f'fill="{FILL["green"]}" {line}/>')
    zx, zy = GRID_X + 1.5 * CELL_W, GRID_Y - ZERO_H / 2 + 6
    out.append(f'<text x="{zx}" y="{zy + 10}" text-anchor="middle" {font} font-size="30" fill="#FFFFFF">0</text>')
    spots["straight:0"] = (zx, zy)
    if result == 0:
        out.append(f'<circle cx="{zx}" cy="{zy}" r="24" fill="none" stroke="{GOLD}" stroke-width="5"/>')
    # 1-36, three across
    for row in range(12):
        for col in range(3):
            n = row * 3 + col + 1
            cell(GRID_X + col * CELL_W, GRID_Y + row * ROW_H, CELL_W, ROW_H, str(n),
                 fill=FILL[R.color(n)], size=28, key=f"straight:{n}")
    # columns along the bottom
    for col in range(3):
        cell(GRID_X + col * CELL_W, GRID_Y + 12 * ROW_H, CELL_W, ROW_H, "2 to 1", size=20, ink="#F2E6C8", key=f"col{col + 1}")
    # dozens down the left
    for d, name in enumerate(("1st 12", "2nd 12", "3rd 12")):
        cell(GRID_X - 80, GRID_Y + d * 4 * ROW_H, 80, 4 * ROW_H, name, size=24, ink="#F2E6C8",
             key=f"dozen{d + 1}", rotate=True)
    # even-money bets on the far left
    for e, (key, text) in enumerate((("low", "1-18"), ("even", "EVEN"), ("red", ""), ("black", ""),
                                     ("odd", "ODD"), ("high", "19-36"))):
        x, y = GRID_X - 160, GRID_Y + e * 2 * ROW_H
        if key in ("red", "black"):
            out.append(f'<rect x="{x}" y="{y}" width="80" height="{2 * ROW_H}" fill="none" {line}/>')
            if wins(key):
                out.append(f'<rect x="{x + 3}" y="{y + 3}" width="74" height="{2 * ROW_H - 6}" fill="none" stroke="{GOLD}" stroke-width="5"/>')
            out.append(f'<polygon points="{x + 40},{y + 18} {x + 64},{y + ROW_H} {x + 40},{y + 2 * ROW_H - 18} {x + 16},{y + ROW_H}" '
                       f'fill="{FILL[key]}" stroke="rgba(255,255,255,0.7)" stroke-width="1.5"/>')
            spots[key] = (x + 40, y + ROW_H)
        else:
            cell(x, y, 80, 2 * ROW_H, text, size=20, ink="#F2E6C8", key=key, rotate=True)

    # the chips, placed clear of each spot's label
    colours = player_colours(table)
    stacks = {}
    for pid, slot in table.players.items():
        for key, amount in slot["bets"].items():
            if key in spots:
                stacks.setdefault(key, []).append((colours[pid], amount))
    for key, chips in stacks.items():
        bx, by = spots[key]
        for j, (colour, amount) in enumerate(chips[:5]):
            if key.startswith("straight:"):
                cx, cy = bx + 34 + j * 9, by - 6 - j * 7
            elif key.startswith("col"):
                cx, cy = bx, by + 34 - j * 7
            elif key.startswith("dozen"):
                cx, cy = bx, by + 62 - j * 7
            elif key in ("red", "black"):
                cx, cy = bx + 22, by + 28 - j * 7
            else:
                cx, cy = bx, by + 40 - j * 7
            won, lost = wins(key), result is not None and not wins(key)
            if won:
                out.append(f'<circle cx="{cx}" cy="{cy}" r="27" fill="{GOLD}" opacity="0.55" filter="url(#glow)"/>')
            out.append(f'<g opacity="{0.35 if lost else 1}"><circle cx="{cx}" cy="{cy + 3}" r="20" fill="#000000" opacity="0.4"/>'
                       f'<circle cx="{cx}" cy="{cy}" r="20" fill="{colour}"/>'
                       f'<circle cx="{cx}" cy="{cy}" r="16" fill="none" stroke="#FFFFFF" stroke-width="3.5" stroke-dasharray="5 4.2"/>'
                       f'<circle cx="{cx}" cy="{cy}" r="11.5" fill="{colour}" stroke="rgba(0,0,0,0.25)"/>'
                       f'<text x="{cx}" y="{cy + 4.5}" text-anchor="middle" font-family="Josefin Sans" font-weight="700" '
                       f'font-size="12" fill="#FFFFFF">{chip_text(amount)}</text></g>')
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">'
            '<defs><filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="5"/></filter></defs>'
            + "".join(out) + "</svg>")


# ---------------------------------------------------------------------------
# The wheel (shown small in the results rail)
# ---------------------------------------------------------------------------
def wheel_svg(result: int, size: int = 200) -> str:
    """A top-down wheel turned so the winning pocket sits under the marker, with the ball in it."""
    R = _rules()
    c = size / 2
    rim, track, num, pocket, turret = c * 0.987, c * 0.85, c * 0.735, c * 0.606, c * 0.148
    step = 360 / 37
    turn = -R.WHEEL_ORDER.index(result) * step
    pocket_fill = {"red": "#7E1820", "black": "#0A0B0D", "green": "#0C5A33"}

    def pt(r, a):
        ar = math.radians(a - 90)
        return c + r * math.cos(ar), c + r * math.sin(ar)

    def sector(r0, r1, a0, a1):
        p1, p2, p3, p4 = pt(r1, a0), pt(r1, a1), pt(r0, a1), pt(r0, a0)
        return (f"M{p1[0]:.2f} {p1[1]:.2f} A{r1:.2f} {r1:.2f} 0 0 1 {p2[0]:.2f} {p2[1]:.2f} L{p3[0]:.2f} {p3[1]:.2f} "
                f"A{r0:.2f} {r0:.2f} 0 0 0 {p4[0]:.2f} {p4[1]:.2f} Z")

    o = [f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" xmlns="http://www.w3.org/2000/svg"><defs>'
         '<radialGradient id="wwood" cx="0.5" cy="0.45" r="0.6"><stop offset="0.7" stop-color="#7A4A26"/><stop offset="0.9" stop-color="#4A2A14"/><stop offset="1" stop-color="#2A160A"/></radialGradient>'
         '<radialGradient id="wtrack" cx="0.5" cy="0.4" r="0.6"><stop offset="0" stop-color="#5C3A20"/><stop offset="1" stop-color="#2E1B0E"/></radialGradient>'
         '<radialGradient id="wcone" cx="0.45" cy="0.4" r="0.6"><stop offset="0" stop-color="#9A6638"/><stop offset="0.7" stop-color="#5E3A1E"/><stop offset="1" stop-color="#3A2210"/></radialGradient>'
         '<radialGradient id="wchrome" cx="0.35" cy="0.3" r="0.8"><stop offset="0" stop-color="#FFFFFF"/><stop offset="0.35" stop-color="#C9CED6"/><stop offset="0.7" stop-color="#7C838E"/><stop offset="1" stop-color="#3A3F47"/></radialGradient>'
         '<radialGradient id="wball" cx="0.35" cy="0.3" r="0.7"><stop offset="0" stop-color="#FFFFFF"/><stop offset="0.6" stop-color="#E8E8E8"/><stop offset="1" stop-color="#9A9A9A"/></radialGradient>'
         '<radialGradient id="wgloss" cx="0.35" cy="0.25" r="0.75"><stop offset="0" stop-color="#FFFFFF" stop-opacity="0.22"/><stop offset="0.6" stop-color="#FFFFFF" stop-opacity="0"/></radialGradient>'
         '<filter id="wglow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="2.5"/></filter>'
         "</defs>"]
    o.append(f'<circle cx="{c}" cy="{c}" r="{rim:.1f}" fill="url(#wwood)"/>')
    o.append(f'<circle cx="{c}" cy="{c}" r="{track:.1f}" fill="url(#wtrack)" stroke="#1C0F06" stroke-width="{max(1, c * 0.01):.1f}"/>')
    o.append(f'<g transform="rotate({turn:.3f} {c} {c})">')
    for i, n in enumerate(R.WHEEL_ORDER):
        a0, a1 = i * step - step / 2, i * step + step / 2
        o.append(f'<path d="{sector(num, track * 0.98, a0, a1)}" fill="{FILL[R.color(n)]}" stroke="#D9C38A" stroke-opacity="0.55" stroke-width="0.6"/>')
        o.append(f'<path d="{sector(pocket, num, a0, a1)}" fill="{pocket_fill[R.color(n)]}"/>')
        tr = (num + track * 0.98) / 2
        tx, ty = pt(tr, i * step)
        o.append(f'<text x="{tx:.1f}" y="{ty:.1f}" transform="rotate({i * step:.2f} {tx:.1f} {ty:.1f})" text-anchor="middle" '
                 f'dominant-baseline="central" font-family="DM Serif Display" font-size="{c * 0.068:.1f}" fill="#FFFFFF">{n}</text>')
        f0, f1 = pt(pocket, a0), pt(num, a0)
        o.append(f'<line x1="{f0[0]:.1f}" y1="{f0[1]:.1f}" x2="{f1[0]:.1f}" y2="{f1[1]:.1f}" stroke="#E6E9EE" stroke-width="{max(0.8, c * 0.007):.1f}"/>')
    for r in (num, pocket):
        o.append(f'<circle cx="{c}" cy="{c}" r="{r:.1f}" fill="none" stroke="#E6E9EE" stroke-width="{max(0.8, c * 0.008):.1f}"/>')
    o.append(f'<circle cx="{c}" cy="{c}" r="{pocket * 0.98:.1f}" fill="url(#wcone)"/>')
    for k in range(4):
        end = pt(turret + c * 0.15, k * 90)
        o.append(f'<line x1="{c}" y1="{c}" x2="{end[0]:.1f}" y2="{end[1]:.1f}" stroke="#AEB4BD" stroke-width="{c * 0.04:.1f}" stroke-linecap="round"/>')
        o.append(f'<circle cx="{end[0]:.1f}" cy="{end[1]:.1f}" r="{c * 0.033:.1f}" fill="url(#wchrome)"/>')
    o.append(f'<circle cx="{c}" cy="{c}" r="{turret:.1f}" fill="url(#wchrome)" stroke="#2A2E35" stroke-width="{max(0.8, c * 0.006):.1f}"/>')
    o.append("</g>")
    # the winning pocket (now at the top), outlined, with the ball in it
    win = sector(pocket, track * 0.98, -step / 2, step / 2)
    o.append(f'<path d="{win}" fill="none" stroke="{GOLD}" stroke-width="{c * 0.04:.1f}" filter="url(#wglow)"/>')
    o.append(f'<path d="{win}" fill="none" stroke="{GOLD}" stroke-width="{c * 0.02:.1f}"/>')
    by = c - (pocket + num) / 2
    o.append(f'<circle cx="{c + c * 0.01:.1f}" cy="{by + c * 0.012:.1f}" r="{c * 0.042:.1f}" fill="#000000" opacity="0.45"/>')
    o.append(f'<circle cx="{c}" cy="{by:.1f}" r="{c * 0.042:.1f}" fill="url(#wball)"/>')
    o.append(f'<circle cx="{c}" cy="{c}" r="{rim:.1f}" fill="url(#wgloss)"/>')
    return "".join(o) + "</svg>"


# ---------------------------------------------------------------------------
# Players and pages
# ---------------------------------------------------------------------------
def roster_html(table, result=None) -> str:
    R = _rules()
    if not table.players:
        return ('<div style="padding:26px 0 6px;text-align:center;font-size:24px;color:rgba(255,255,255,0.6)">'
                'No bets yet - tap Enter Table to put chips down</div>')
    colours = player_colours(table)

    def net(bets):
        return R._resolve(bets, result) - sum(bets.values())

    order = sorted(table.players.items(),
                   key=lambda kv: -(net(kv[1]["bets"]) if result is not None else sum(kv[1]["bets"].values())))
    rows = []
    for pid, slot in order:
        bets = slot["bets"]
        if result is None:
            detail = " · ".join(f"{R.bet_label(k)} {a:,}" for k, a in bets.items())
            right = f'<span class="serif" style="font-size:34px;color:#FFFFFF">{sum(bets.values()):,}</span>'
        else:
            v = net(bets)
            ink = "#4ADE80" if v > 0 else ("#F87171" if v < 0 else "rgba(255,255,255,0.8)")
            detail = " · ".join(f"{R.bet_label(k)} {'won' if R.bet_wins(k, result) else 'lost'}" for k in bets)
            right = f'<span class="serif" style="font-size:38px;color:{ink}">{felt.signed(v)}</span>'
        rows.append(
            '<div style="display:flex;align-items:center;gap:16px;padding:14px 0;border-top:1px solid rgba(255,255,255,0.1)">'
            f'<div style="width:26px;height:26px;flex:none;border-radius:50%;background:{colours[pid]};'
            'box-shadow:0 0 0 3px rgba(255,255,255,0.85) inset"></div>'
            '<div style="flex:1;min-width:0">'
            f'<div class="ellipsis" style="font-size:22px;font-weight:600;letter-spacing:0.12em;text-transform:uppercase">{felt.esc(slot["name"])}</div>'
            f'<div class="ellipsis" style="margin-top:4px;font-size:19px;color:rgba(255,255,255,0.55)">{felt.esc(detail)}</div>'
            f'</div>{right}</div>')
    return f'<div style="display:flex;flex-direction:column;margin-top:18px">{"".join(rows)}</div>'


def _layout_block(table, result=None) -> str:
    return f'<div style="display:flex;justify-content:center">{layout_svg(table, result)}</div>'


def build_table_html(table) -> str:
    bets = sum(len(s["bets"]) for s in table.players.values())
    ledger = [("Pot", f"{table.pot:,}"), ("Players", str(len(table.players))), ("Bets", str(bets))]
    rail = felt.rail("Place your bets", "Tap Enter Table to put chips down", ledger=ledger)
    return felt.build_page("European Roulette", "Bets open", _layout_block(table) + roster_html(table), rail)


def build_results_html(table) -> str:
    R = _rules()
    n = table.result
    if n == 0:
        head, tags = "0 · Green", "Zero"
    else:
        head = f"{n} · {R.color(n).capitalize()}"
        tags = " · ".join((R.color(n).capitalize(), "Even" if n % 2 == 0 else "Odd", "1-18" if n <= 18 else "19-36"))
    paid = sum(R._resolve(s["bets"], n) for s in table.players.values())
    ledger = [("Pot", f"{table.pot:,}"), ("Paid out", f"{paid:,}"), ("Players", str(len(table.players)))]
    rail = felt.rail(head, tags, side_html=wheel_svg(n), ledger=ledger)
    return felt.build_page("European Roulette", "Round over", _layout_block(table, n) + roster_html(table, n), rail)
