"""The Glass Bridge board: the bridge in perspective, lit like a game-show arena.

`bridge_svg` draws the crossing as one SVG (rendered by headless Chrome through the shared
casino felt page). It only ever shows what the player has found out: the pane they stood on
in each crossed row, and in the row that broke, the hole plus the side that would have held.
Every pane they haven't reached looks the same whichever side is safe.
"""

from __future__ import annotations

import random

W, H = 820, 980
CX = 372                     # the bridge sits left of centre to leave room for the multipliers
S = 252                      # screen half-width of the bridge at depth 1
L, G = 0.13, 0.025           # pane depth and cross-beam depth (world units)
Y_NEAR, Y_FAR = 860, 190     # screen y of the nearest and farthest row edges
GAP = 0.05                   # half-width of the centre beam

STEEL_TOP, STEEL_TOP2, STEEL_SIDE, RIVET = "#D9DEE5", "#9AA3AF", "#3B414B", "#FFFFFF"
GLASS = ("rgba(235,245,250,0.55)", "rgba(200,220,230,0.22)", "rgba(255,255,255,0.9)")
SAFE = ("rgba(120,255,190,0.60)", "rgba(40,200,120,0.30)", "#B5FFD8")
LIVE = ("rgba(255,255,255,0.82)", "rgba(220,235,245,0.45)", "#FFFFFF")
CROSSED = "rgba(230,240,245,0.10)"
ACCENT = "#FF3D7F"
DECK = "#20242B"


class _Projection:
    """World (x across the bridge, z depth away from the viewer) to screen."""

    def __init__(self, steps: int):
        self.steps = steps
        self.zmax = 1 + steps * (L + G)
        self.k = (Y_NEAR - Y_FAR) / (1 - 1 / self.zmax)
        self.h0 = Y_NEAR - self.k

    def x(self, xw, z):
        return CX + xw * S / z

    def y(self, z):
        return self.h0 + self.k / z

    def quad(self, x0, x1, z0, z1):
        return [(self.x(x0, z0), self.y(z0)), (self.x(x1, z0), self.y(z0)),
                (self.x(x1, z1), self.y(z1)), (self.x(x0, z1), self.y(z1))]

    def row(self, i):
        zn = 1 + i * (L + G)
        return zn, zn + L


def _pts(seq):
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in seq)


def _defs() -> str:
    def grad(name, top, bot):
        return (f'<linearGradient id="{name}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{top}"/>'
                f'<stop offset="1" stop-color="{bot}"/></linearGradient>')

    blur = "".join(f'<filter id="blur{n}" x="-50%" y="-50%" width="200%" height="200%">'
                   f'<feGaussianBlur stdDeviation="{n}"/></filter>' for n in (2, 6, 14))
    return (
        "<defs>"
        '<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#000000"/>'
        '<stop offset="0.6" stop-color="#05060A"/><stop offset="1" stop-color="#000000"/></linearGradient>'
        + blur + grad("glass", GLASS[0], GLASS[1]) + grad("safe", SAFE[0], SAFE[1]) + grad("live", LIVE[0], LIVE[1])
        + f'<linearGradient id="steel" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{STEEL_TOP2}"/>'
        f'<stop offset="0.5" stop-color="{STEEL_TOP}"/><stop offset="1" stop-color="{STEEL_TOP2}"/></linearGradient>'
        '<linearGradient id="sheen" x1="0" y1="1" x2="1" y2="0"><stop offset="0.35" stop-color="#FFFFFF" stop-opacity="0"/>'
        '<stop offset="0.5" stop-color="#FFFFFF" stop-opacity="0.35"/><stop offset="0.62" stop-color="#FFFFFF" stop-opacity="0"/></linearGradient>'
        '<linearGradient id="fog" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#000000" stop-opacity="0.9"/>'
        '<stop offset="1" stop-color="#000000" stop-opacity="0"/></linearGradient>'
        '<radialGradient id="spot" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stop-color="#FFFFFF" stop-opacity="0.2"/>'
        '<stop offset="1" stop-color="#FFFFFF" stop-opacity="0"/></radialGradient>'
        f'<radialGradient id="finish" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stop-color="{ACCENT}" stop-opacity="0.55"/>'
        f'<stop offset="1" stop-color="{ACCENT}" stop-opacity="0"/></radialGradient>'
        "</defs>"
    )


def _figure(x, y, scale) -> str:
    """The player: a small figure with a shadow, standing at (x, y)."""
    return (f'<ellipse cx="{x:.1f}" cy="{y + 2:.1f}" rx="{16 * scale:.1f}" ry="{5 * scale:.1f}" fill="#000000" opacity="0.45"/>'
            f'<g transform="translate({x:.1f} {y:.1f}) scale({scale:.2f})" fill="{ACCENT}">'
            '<circle cx="0" cy="-58" r="9"/>'
            '<path d="M-11 -46 Q0 -50 11 -46 L13 -18 L8 -18 L7 0 L1 0 L0 -20 L-1 0 L-7 0 L-8 -18 L-13 -18 Z"/></g>')


def bridge_svg(*, steps: int, step: int, safe_sides, multipliers, playing: bool,
               fell_on: str | None = None) -> str:
    """The board for one moment of a crossing.

    step: panels crossed. playing: the game is still live. fell_on: the side that broke
    (the row that broke is `step`). safe_sides[i] is only read for rows the player has
    already found out about."""
    p = _Projection(steps)
    fatal = step if fell_on else None
    across = step >= steps
    z_end = p.zmax + 0.32
    o = [_defs(), f'<rect width="{W}" height="{H}" fill="url(#sky)"/>']

    # spotlight beams from the roof
    for sx in (150, 372, 594):
        o.append(f'<polygon points="{sx - 30},0 {sx + 30},0 {sx + 180},{H} {sx - 180},{H}" fill="#FFFFFF" opacity="0.045"/>')

    # far platform, with its row of lights
    o.append(f'<ellipse cx="{CX}" cy="{p.y(z_end):.0f}" rx="250" ry="70" fill="url(#finish)"/>')
    o.append(f'<polygon points="{_pts(p.quad(-1.18, 1.18, p.zmax, z_end))}" fill="{DECK}"/>')
    o.append(f'<line x1="{p.x(-1.18, p.zmax):.1f}" y1="{p.y(p.zmax):.1f}" x2="{p.x(1.18, p.zmax):.1f}" '
             f'y2="{p.y(p.zmax):.1f}" stroke="{ACCENT}" stroke-width="3"/>')
    for k in range(9):
        o.append(f'<circle cx="{p.x(-1.0 + k * 0.25, p.zmax + 0.05):.1f}" cy="{p.y(p.zmax + 0.05):.1f}" r="3" '
                 f'fill="{ACCENT}" filter="url(#blur2)"/>')

    # a spotlight on the row about to be stepped on
    if playing and not across:
        zn, zf = p.row(step)
        zc = (zn + zf) / 2
        o.append(f'<ellipse cx="{CX}" cy="{p.y(zc):.0f}" rx="{S / zc * 1.35:.0f}" '
                 f'ry="{(p.y(zc - L) - p.y(zc + L)) * 1.1:.0f}" fill="url(#spot)"/>')

    # the deck, far rows first so nearer ones overlap them
    for i in reversed(range(steps)):
        zn, zf = p.row(i)
        cb0, cb1 = zf, zf + G
        o.append(f'<polygon points="{_pts(p.quad(-1.0, 1.0, cb0, cb1))}" fill="url(#steel)"/>')
        face = 0.05 * S / cb0
        o.append(f'<polygon points="{_pts([(p.x(-1.0, cb0), p.y(cb0)), (p.x(1.0, cb0), p.y(cb0)), (p.x(1.0, cb0), p.y(cb0) + face), (p.x(-1.0, cb0), p.y(cb0) + face)])}" fill="{STEEL_SIDE}"/>')
        for side in ("L", "R"):
            sg = -1 if side == "L" else 1
            q = p.quad(min(sg * 0.985, sg * (GAP + 0.015)), max(sg * 0.985, sg * (GAP + 0.015)), zn, zf)
            if i < step:
                stood = safe_sides[i] == side
                fill, edge, width = ("url(#safe)", SAFE[2], 2.2) if stood else (CROSSED, GLASS[2], 1.0)
            elif fatal is not None and i == fatal:
                if side == fell_on:
                    o.append(_broken_pane(q, i))
                    continue
                fill, edge, width = "url(#safe)", SAFE[2], 2.0      # where they should have gone
            elif playing and i == step:
                fill, edge, width = "url(#live)", LIVE[2], 2.6
                o.append(f'<polygon points="{_pts(q)}" fill="none" stroke="{LIVE[2]}" stroke-width="10" '
                         f'opacity="0.35" filter="url(#blur6)"/>')
            else:
                fill, edge, width = "url(#glass)", GLASS[2], 1.2
            o.append(f'<polygon points="{_pts(q)}" fill="{fill}" stroke="{edge}" stroke-width="{width}" stroke-linejoin="round"/>')
            o.append(f'<polygon points="{_pts(q)}" fill="url(#sheen)" opacity="{0.9 if i >= step else 0.4}"/>')
            o.append(f'<line x1="{q[0][0]:.1f}" y1="{q[0][1] - 1:.1f}" x2="{q[1][0]:.1f}" y2="{q[1][1] - 1:.1f}" '
                     'stroke="#FFFFFF" stroke-opacity="0.5" stroke-width="1.2"/>')
        o.append(f'<polygon points="{_pts(p.quad(-GAP, GAP, zn - 0.002, zf + 0.002))}" fill="url(#steel)"/>')

    # side girders (top and outer faces) with rivets
    z0 = 0.93
    for sg in (-1, 1):
        a, b = sg * 1.0, sg * 1.13
        top = [(p.x(a, z0), p.y(z0)), (p.x(b, z0), p.y(z0)), (p.x(b, z_end), p.y(z_end)), (p.x(a, z_end), p.y(z_end))]
        o.append(f'<polygon points="{_pts(top)}" fill="url(#steel)"/>')
        drop = 0.16 * S
        outer = [(p.x(b, z0), p.y(z0)), (p.x(b, z_end), p.y(z_end)),
                 (p.x(b, z_end), p.y(z_end) + drop / z_end), (p.x(b, z0), p.y(z0) + drop / z0)]
        o.append(f'<polygon points="{_pts(outer)}" fill="{STEEL_SIDE}"/>')
        for i in range(steps + 1):
            z = 1 + i * (L + G) - G / 2
            o.append(f'<circle cx="{p.x(sg * 1.065, z):.1f}" cy="{p.y(z):.1f}" r="{3.2 / z:.1f}" fill="{RIVET}"/>')

    # railings: a post at every row and a handrail along the tops
    for sg in (-1, 1):
        tops = []
        for i in range(steps + 2):
            z = min(0.95 + i * (L + G), z_end)
            bx, by = p.x(sg * 1.11, z), p.y(z)
            post = 0.30 * S / z
            o.append(f'<line x1="{bx:.1f}" y1="{by:.1f}" x2="{bx:.1f}" y2="{by - post:.1f}" stroke="{STEEL_TOP}" '
                     f'stroke-width="{max(1.5, 4.5 / z):.1f}" stroke-linecap="round"/>')
            tops.append((bx, by - post, z))
        o.append(f'<polyline points="{_pts([(x, y) for x, y, _ in tops])}" fill="none" stroke="{STEEL_TOP}" stroke-width="3" stroke-linejoin="round"/>')
        mids = [(x, y + 0.15 * S / z) for x, y, z in tops]
        o.append(f'<polyline points="{_pts(mids)}" fill="none" stroke="{STEEL_TOP2}" stroke-width="1.6" opacity="0.8"/>')

    # near platform
    o.append(f'<polygon points="{_pts([(p.x(-1.25, 0.86), p.y(0.86)), (p.x(1.25, 0.86), p.y(0.86)), (p.x(1.25, 0.93), p.y(0.93)), (p.x(-1.25, 0.93), p.y(0.93))])}" fill="{DECK}"/>')
    o.append(f'<line x1="{p.x(-1.13, 0.93):.1f}" y1="{p.y(0.93):.1f}" x2="{p.x(1.13, 0.93):.1f}" y2="{p.y(0.93):.1f}" '
             f'stroke="{ACCENT}" stroke-width="3"/>')

    # the player
    if fatal is None:
        if across:
            z = (p.zmax + z_end) / 2
            o.append(_figure(p.x(0, z), p.y(z), 1.25 / z))
        elif step > 0:
            zn, zf = p.row(step - 1)
            zc = (zn + zf) / 2
            sg = -1 if safe_sides[step - 1] == "L" else 1
            o.append(_figure(p.x(sg * 0.52, zc), p.y(zc), 1.25 / zc))
        else:
            o.append(_figure(p.x(0, 0.965), p.y(0.965), 1.25 / 0.965))   # waiting on the start deck

    # the multiplier for each row, on a dark plaque
    for i in range(steps):
        zn, zf = p.row(i)
        size = 18 + 26 / zn
        if fatal is not None and i == fatal:
            colour = "#FF6B6B"
        elif i < step:
            colour = "#FFFFFF"
        elif playing and i == step:
            colour = "#FFFFFF"
        else:
            colour = "rgba(255,255,255,0.45)"
        text = f"{multipliers[i]:.2f}×"
        lx, ly = p.x(1.13, zn) + 22, (p.y(zn) + p.y(zf)) / 2
        o.append(f'<rect x="{lx - 9:.1f}" y="{ly - size * 0.62:.1f}" width="{size * 0.47 * len(text) + 16:.0f}" '
                 f'height="{size * 1.2:.0f}" rx="6" fill="rgba(0,0,0,0.7)"/>')
        o.append(f'<text x="{lx:.1f}" y="{ly + size * 0.34:.1f}" font-family="DM Serif Display" '
                 f'font-size="{size:.0f}" fill="{colour}">{text}</text>')

    # haze over the far end
    o.append(f'<rect x="0" y="0" width="{W}" height="{p.y(1 + 5 * (L + G)):.0f}" fill="url(#fog)" opacity="0.5"/>')
    return f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">{"".join(o)}</svg>'


def _broken_pane(q, row) -> str:
    """A pane that gave way: a glowing red hole, jagged shards left in the frame, and
    pieces falling away into the dark."""
    rnd = random.Random(row)   # the same break every time this board is redrawn
    cx = sum(x for x, _ in q) / 4
    cy = (q[0][1] + q[2][1]) / 2
    out = [f'<polygon points="{_pts(q)}" fill="#FF3B3B" opacity="0.18"/>',
           f'<polygon points="{_pts(q)}" fill="none" stroke="#FF4D4D" stroke-width="9" opacity="0.6" filter="url(#blur6)"/>',
           f'<polygon points="{_pts(q)}" fill="none" stroke="#FF6B6B" stroke-width="2"/>']
    for a, b in ((q[0], q[1]), (q[1], q[2]), (q[2], q[3]), (q[3], q[0])):
        for f in (0.0, 0.35, 0.7):
            px, py = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
            nx, ny = a[0] + (b[0] - a[0]) * (f + 0.3), a[1] + (b[1] - a[1]) * (f + 0.3)
            tip = (px + (cx - px) * rnd.uniform(0.25, 0.5), py + (cy - py) * rnd.uniform(0.25, 0.5))
            out.append(f'<polygon points="{_pts([(px, py), (nx, ny), tip])}" fill="{GLASS[0]}" stroke="{GLASS[2]}" stroke-width="1"/>')
    for k in range(10):
        d = k / 10
        fx = cx + rnd.uniform(-1, 1) * 40 * (1 - d)
        fy = cy + rnd.uniform(-1, 1) * 14 * (1 - d)
        size = 13 * (1 - d) + 2
        out.append(f'<polygon points="{_pts([(fx, fy), (fx + size, fy + size * 0.4), (fx + size * 0.3, fy + size)])}" '
                   f'fill="{GLASS[0]}" stroke="{GLASS[2]}" stroke-width="0.8" opacity="{0.85 - d * 0.6:.2f}"/>')
    return "".join(out)
