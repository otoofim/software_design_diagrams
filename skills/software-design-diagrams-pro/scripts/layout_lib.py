"""
layout_lib.py - shared layout engine for the interactive HTML diagram generator.

Contains:
  * text measurement + box fitting (approximate, font-agnostic)
  * style helpers (a compact internal attribute encoding consumed by emit_html)
  * Page: an in-memory diagram model (neutral Cell objects — vertices and edges
    with absolute geometry and routed waypoints). The HTML emitter reads this
    directly; nothing here writes files.
  * layered_layout: Sugiyama-style layered layout (cycle breaking, ranking,
    crossing reduction, isotonic coordinate assignment, component packing)
  * nested_layout: recursive layout for containers (groups, packages, nodes, composite states)
  * lane_layout: swimlane (partition) layout for flowcharts / activity diagrams
"""
from __future__ import annotations

import html
import heapq
import math
import re
from collections import defaultdict, deque

# --------------------------------------------------------------------------- text

FONT_SIZE = 12


def esc(text) -> str:
    """Escape user text for an html=1 label and turn newlines into <br>."""
    if text is None:
        return ""
    return html.escape(str(text), quote=False).replace("\n", "<br>")


def _char_w(font_size=FONT_SIZE, bold=False):
    return font_size * 0.6 * (1.08 if bold else 1.0)


def text_width(text, font_size=FONT_SIZE, bold=False) -> float:
    lines = str(text or "").split("\n")
    return max((len(l) for l in lines), default=0) * _char_w(font_size, bold)


def text_lines(text, max_text_w, font_size=FONT_SIZE, bold=False) -> int:
    cw = _char_w(font_size, bold)
    n = 0
    for line in str(text or "").split("\n"):
        need = len(line) * cw
        n += max(1, math.ceil(need / max(max_text_w, 1)))
    return n


def fit_box(text, min_w=120, min_h=50, max_w=240, pad_w=28, pad_h=22,
            font_size=FONT_SIZE, bold=False, grid=10):
    """Return (w, h) that comfortably fits the text, wrapping at max_w."""
    longest = text_width(text, font_size, bold)
    w = min(max_w, max(min_w, longest + pad_w))
    lines = text_lines(text, w - pad_w, font_size, bold)
    h = max(min_h, lines * font_size * 1.4 + pad_h)
    return (math.ceil(w / grid) * grid, math.ceil(h / grid) * grid)


def slug(text) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", str(text)).strip("-").lower()
    return s or "diagram"


# ------------------------------------------------------------------------- styles

def parse_style(style: str):
    items = []
    for part in (style or "").split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            items.append((k.strip(), v))
        else:
            items.append((part, None))
    return items


def merge_style(base: str, override: str | None) -> str:
    """Merge two style strings; keys in override win. Bare tokens are kept once."""
    if not override:
        return base
    items = parse_style(base)
    index = {k: i for i, (k, _) in enumerate(items)}
    for k, v in parse_style(override):
        if k in index:
            items[index[k]] = (k, v)
        else:
            index[k] = len(items)
            items.append((k, v))
    return "".join(f"{k};" if v is None else f"{k}={v};" for k, v in items)


def style_has(style: str, key: str, value: str | None = None) -> bool:
    for k, v in parse_style(style):
        if k == key and (value is None or v == value):
            return True
    return False


PALETTE = {
    "blue": ("#dae8fc", "#6c8ebf"),
    "green": ("#d5e8d4", "#82b366"),
    "yellow": ("#fff2cc", "#d6b656"),
    "orange": ("#ffe6cc", "#d79b00"),
    "red": ("#f8cecc", "#b85450"),
    "purple": ("#e1d5e7", "#9673a6"),
    "gray": ("#f5f5f5", "#666666"),
    "white": ("#ffffff", "#000000"),
}


def colors(name):
    fill, stroke = PALETTE.get(name, PALETTE["white"])
    return f"fillColor={fill};strokeColor={stroke};"


# --------------------------------------------------------------------------- routing

def _box_contains(outer, inner, pad=0.5):
    """True if `inner` box lies fully inside `outer` box (used to recognise
    ancestor containers so they're never treated as routing obstacles)."""
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    return (ox - pad <= ix and iy >= oy - pad and
            ix + iw <= ox + ow + pad and iy + ih <= oy + oh + pad)


def _side_point(box, side, frac=0.5):
    x, y, w, h = box
    if side == "left":
        return (x, y + h * frac)
    if side == "right":
        return (x + w, y + h * frac)
    if side == "top":
        return (x + w * frac, y)
    return (x + w * frac, y + h)  # bottom


def _exit_side(box, towards):
    """Which side of `box` faces the point `towards` best."""
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    dx, dy = towards[0] - cx, towards[1] - cy
    if abs(dx) / max(w, 1) >= abs(dy) / max(h, 1):
        return "right" if dx >= 0 else "left"
    return "bottom" if dy >= 0 else "top"


def _seg_hits_box(x0, y0, x1, y1, box, margin):
    """Does the axis-aligned segment (x0,y0)-(x1,y1) pass through the
    interior of `box` (inflated by `margin`)? Touching the edge is fine."""
    bx, by, bw, bh = box
    bx, by, bw, bh = bx - margin, by - margin, bw + 2 * margin, bh + 2 * margin
    if x0 == x1:
        x = x0
        if not (bx < x < bx + bw):
            return False
        ylo, yhi = (y0, y1) if y0 <= y1 else (y1, y0)
        return yhi > by and ylo < by + bh
    else:
        y = y0
        if not (by < y < by + bh):
            return False
        xlo, xhi = (x0, x1) if x0 <= x1 else (x1, x0)
        return xhi > bx and xlo < bx + bw


def _path_clear(points, obstacles, margin):
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        for box in obstacles:
            if _seg_hits_box(x0, y0, x1, y1, box, margin):
                return False
    return True


def _anchor_style(exit_side, entry_side, exit_frac=0.5, entry_frac=0.5):
    """exitX/exitY/entryX/entryY style fragment that pins an edge's endpoints
    to the exact side-midpoints `route_orthogonal` used to build its path. The
    HTML emitter reads these to attach the connector to the precise border
    point the route assumed, so the first/last segment stays axis-aligned
    instead of cutting diagonally from the box centre."""
    def coord(side, f):
        return {"left": (0, f), "right": (1, f), "top": (f, 0), "bottom": (f, 1)}[side]
    ex, ey = coord(exit_side, round(exit_frac, 4))
    nx, ny = coord(entry_side, round(entry_frac, 4))
    return (f"exitX={ex};exitY={ey};exitDx=0;exitDy=0;"
            f"entryX={nx};entryY={ny};entryDx=0;entryDy=0;")


def _cross_count(path, existing):
    """How many already-routed segments `path` crosses, and how far it runs on
    top of one (collinear overlap). Both make a diagram misleading: a reader
    can't tell which line continues where."""
    crosses, overlap = 0, 0.0
    for (ax, ay), (bx, by) in zip(path, path[1:]):
        h = ay == by
        for (cx, cy), (dx, dy) in existing:
            if (cy == dy) == h:
                if h and abs(ay - cy) < 4:
                    lo, hi = max(min(ax, bx), min(cx, dx)), min(max(ax, bx), max(cx, dx))
                    overlap += max(0.0, hi - lo)
                elif not h and abs(ax - cx) < 4:
                    lo, hi = max(min(ay, by), min(cy, dy)), min(max(ay, by), max(cy, dy))
                    overlap += max(0.0, hi - lo)
                continue
            if h:
                x, y = cx, ay
                if min(ax, bx) < x < max(ax, bx) and min(cy, dy) < y < max(cy, dy):
                    crosses += 1
            else:
                x, y = ax, cy
                if min(cx, dx) < x < max(cx, dx) and min(ay, by) < y < max(ay, by):
                    crosses += 1
    return crosses, overlap


#: routing cost of one crossing / one px of shared line, in px of extra length
CROSS_COST = 400
OVERLAP_COST = 3


def _path_cost(path, existing):
    c, o = _cross_count(path, existing)
    length = sum(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(path, path[1:]))
    return c * CROSS_COST + o * OVERLAP_COST + length + 40 * (len(path) - 2)


def route_orthogonal(sbox, tbox, obstacles, margin=14, existing=None, ports=None,
                     strict=False):
    """Orthogonal path from `sbox` to `tbox` (each `(x, y, w, h)`, absolute)
    that avoids every box in `obstacles`. Returns `(waypoints, anchor_style)`:
    `waypoints` is a list of intermediate `(x, y)` points for an edge's
    `points` (empty list if a direct route needs no bends at all);
    `anchor_style` is a style fragment that MUST be merged into the edge's
    style so it actually leaves/enters at the points this function assumed
    (see `_anchor_style`). Returns `(None, None)` if even a maze search
    can't clear every obstacle (caller falls back to a plain auto-routed
    edge).

    Two-stage strategy: first try the handful of simple 1-2 bend "elbow"
    paths every orthogonal router would prefer (cheap, and clean-looking);
    only fall back to a full visibility-grid search when something is
    actually in the way, which keeps most edges simple while still finding a
    clear route through cluttered diagrams.
    """
    sx, sy, sw, sh = sbox
    tx, ty, tw, th = tbox
    scx, scy = sx + sw / 2, sy + sh / 2
    tcx, tcy = tx + tw / 2, ty + th / 2

    if ports:
        (exit_side, ef), (entry_side, nf) = ports
    else:
        exit_side, ef = _exit_side(sbox, (tcx, tcy)), 0.5
        entry_side, nf = _exit_side(tbox, (scx, scy)), 0.5
    sp = _side_point(sbox, exit_side, ef)
    tp = _side_point(tbox, entry_side, nf)
    anchor = _anchor_style(exit_side, entry_side, ef, nf)

    candidates = []
    # straight line (only valid if both points share an axis)
    if sp[0] == tp[0] or sp[1] == tp[1]:
        candidates.append([sp, tp])
    # single-bend "L" paths, elbow at the far side's coordinate
    horiz_exit = exit_side in ("left", "right")
    if horiz_exit:
        candidates.append([sp, (tp[0], sp[1]), tp])
    else:
        candidates.append([sp, (sp[0], tp[1]), tp])
    horiz_entry = entry_side in ("left", "right")
    if horiz_entry:
        candidates.append([sp, (sp[0], tp[1]), tp])
    else:
        candidates.append([sp, (tp[0], sp[1]), tp])
    # two-bend "Z" path through the midpoint between the two shapes
    if horiz_exit:
        mid = (sp[0] + tp[0]) / 2
        candidates.append([sp, (mid, sp[1]), (mid, tp[1]), tp])
    else:
        mid = (sp[1] + tp[1]) / 2
        candidates.append([sp, (sp[0], mid), (tp[0], mid), tp])

    existing = existing or []
    clear = [p for p in candidates if _path_clear(p, obstacles, margin)]
    best = min(clear, key=lambda p: _path_cost(p, existing), default=None)
    if best is not None and _cross_count(best, existing) == (0, 0.0):
        return best[1:-1], anchor
    # an elbow would cross or ride on an earlier connector (or is blocked):
    # search the grid too, then keep whichever route misleads least
    found = _grid_route(sp, tp, sbox, tbox, obstacles, margin, existing,
                        (exit_side, entry_side), strict)
    if found is not None and (best is None or
                              _path_cost(found, existing) < _path_cost(best, existing)):
        return found[1:-1], _anchor_for(found, sbox, tbox)
    if best is not None:
        return best[1:-1], anchor
    return None, None


def _anchor_for(path, sbox, tbox):
    """Anchor style for a route whose endpoints lie on the shapes' borders."""
    def side(box, pt):
        x, y, w, h = box
        if abs(pt[0] - x) < 0.5:
            return "left", (pt[1] - y) / max(h, 1)
        if abs(pt[0] - x - w) < 0.5:
            return "right", (pt[1] - y) / max(h, 1)
        if abs(pt[1] - y) < 0.5:
            return "top", (pt[0] - x) / max(w, 1)
        return "bottom", (pt[0] - x) / max(w, 1)
    (es, ef), (ns, nf) = side(sbox, path[0]), side(tbox, path[-1])
    return _anchor_style(es, ns, ef, nf)


def _grid_route(sp, tp, sbox, tbox, obstacles, margin, existing, sides=None, strict=False):
    """Visibility-grid search from `sp` to `tp` around `obstacles`, also
    trying every side of both shapes and charging for crossing or riding on
    `existing` segments. Returns the full point list or None."""
    sx, sy, sw, sh = sbox
    tx, ty, tw, th = tbox

    pad = 260
    region = (min(sx, tx) - pad, min(sy, ty) - pad,
              max(sx + sw, tx + tw) + pad, max(sy + sh, ty + th) + pad)

    def in_region(box):
        x, y, w, h = box
        return not (x + w < region[0] or x > region[2] or y + h < region[1] or y > region[3])

    local_obs = [b for b in obstacles if in_region(b)]
    order = ("left", "right", "top", "bottom")
    out_dir = {"left": (-1, 0), "right": (1, 0), "top": (0, -1), "bottom": (0, 1)}
    # the assigned port on its side, plus the midpoints of the other sides
    starts = [(sp if sides and k == sides[0] else _side_point(sbox, k), k) for k in order]
    goals = [(tp if sides and k == sides[1] else _side_point(tbox, k), k) for k in order]
    # leave each shape straight out of the side, so the first and last
    # segments are perpendicular to it
    stub = {}
    for pts in (starts, goals):
        for p, k in pts:
            dx, dy = out_dir[k]
            stub[p] = (p[0] + dx * (margin + 2), p[1] + dy * (margin + 2))
    if strict:  # the caller's ports are fixed (e.g. an ER row): no other side
        starts = [(p, k) for p, k in starts if k == sides[0]]
        goals = [(p, k) for p, k in goals if k == sides[1]]
    starts = [p for p, _ in starts]
    goals = [p for p, _ in goals]

    xs = {p[0] for p in stub.values()} | {p[0] for p in starts + goals}
    ys = {p[1] for p in stub.values()} | {p[1] for p in starts + goals}
    for bx, by, bw, bh in local_obs + [sbox, tbox]:
        for x in (bx - margin - 1, bx + bw + margin + 1):
            xs.add(x)
        for y in (by - margin - 1, by + bh + margin + 1):
            ys.add(y)
    # channels halfway between neighbouring coordinates give parallel
    # connectors somewhere to go besides on top of each other
    for vals in (xs, ys):
        srt = sorted(vals)
        vals.update((a + b) / 2 for a, b in zip(srt, srt[1:]) if b - a > 2 * margin)
    xs, ys = sorted(xs), sorted(ys)
    if len(xs) * len(ys) > 6000:
        return None
    blockers = local_obs + [sbox, tbox]

    def seg_ok(x0, y0, x1, y1):
        return not any(_seg_hits_box(x0, y0, x1, y1, b, margin if b not in (sbox, tbox) else 0)
                       for b in blockers)

    def seg_cost(a, b):
        c, o = _cross_count([a, b], existing)
        return abs(a[0] - b[0]) + abs(a[1] - b[1]) + c * CROSS_COST + o * OVERLAP_COST

    xi = {x: i for i, x in enumerate(xs)}
    yi = {y: i for i, y in enumerate(ys)}
    goal_of = {stub[g]: g for g in goals}
    dist, prev, pq = {}, {}, []
    for s0 in starts:
        n = stub[s0]
        if not seg_ok(*s0, *n):
            continue
        d0 = seg_cost(s0, n)
        key = (n, "h" if s0[1] == n[1] else "v")
        if d0 < dist.get(key, float("inf")):
            dist[key], prev[key] = d0, (s0, None)
            heapq.heappush(pq, (d0, key))
    best_end, best_d = None, float("inf")
    done = set()
    while pq:
        d, key = heapq.heappop(pq)
        if key in done or d >= best_d:
            continue
        done.add(key)
        p, dirn = key
        if p in goal_of:
            g = goal_of[p]
            if seg_ok(*p, *g):
                tot = d + seg_cost(p, g) + (40 if dirn != ("h" if p[1] == g[1] else "v") else 0)
                if tot < best_d:
                    best_d, best_end = tot, key
        x, y = p
        steps = []
        if x in xi:
            for j in (yi.get(y, -1) - 1, yi.get(y, -1) + 1):
                if y in yi and 0 <= j < len(ys):
                    steps.append(((x, ys[j]), "v"))
        if y in yi:
            for j in (xi.get(x, -1) - 1, xi.get(x, -1) + 1):
                if x in xi and 0 <= j < len(xs):
                    steps.append(((xs[j], y), "h"))
        for q, nd_dir in steps:
            if not seg_ok(x, y, q[0], q[1]):
                continue
            nd = d + seg_cost(p, q) + (40 if nd_dir != dirn else 0)
            nk = (q, nd_dir)
            if nd < dist.get(nk, float("inf")):
                dist[nk], prev[nk] = nd, (p, key)
                heapq.heappush(pq, (nd, nk))
    if best_end is None:
        return None
    path = [goal_of[best_end[0]]]
    key = best_end
    while key is not None:
        p, pk = prev[key]
        path.append(key[0])
        if pk is None:
            path.append(p)
        key = pk
    path.reverse()
    out = [path[0]]
    for i in range(1, len(path) - 1):
        a, b, c = out[-1], path[i], path[i + 1]
        if (a[0] == b[0] == c[0]) or (a[1] == b[1] == c[1]) or b == a:
            continue
        out.append(b)
    out.append(path[-1])
    return out


def _centre_dist(a, b):
    return abs(a[0] + a[2] / 2 - b[0] - b[2] / 2) + abs(a[1] + a[3] / 2 - b[1] - b[3] / 2)


# --------------------------------------------------------------------------- page

class Cell:
    """A placed vertex or edge in a diagram. Neutral in-memory record consumed
    by the HTML emitter — no XML, no file format.

    Common: `id`, `kind` ("vertex"|"edge"), `value` (HTML label markup),
    `style` (compact internal attribute string; see the style helpers), `parent`.
    Vertex: `x, y, w, h` (coords relative to parent).
    Edge: `source`, `target` (cell ids), `points` (routed waypoints),
          `source_point`/`target_point` (free endpoints when unanchored),
          `relative` (True for edges and edge labels — they have no box bounds).
    """
    __slots__ = ("id", "kind", "value", "style", "parent", "x", "y", "w", "h",
                 "source", "target", "points", "source_point", "target_point",
                 "relative")

    def __init__(self, cid, kind, value="", style="", parent="1"):
        self.id = cid
        self.kind = kind
        self.value = value or ""
        self.style = style or ""
        self.parent = parent
        self.x = self.y = self.w = self.h = 0.0
        self.source = self.target = None
        self.points = []
        self.source_point = self.target_point = None
        self.relative = (kind == "edge")

    @property
    def is_vertex(self):
        return self.kind == "vertex"

    @property
    def is_edge(self):
        return self.kind == "edge"


class Page:
    """One diagram: a flat list of placed Cells plus their absolute geometry."""

    def __init__(self, name: str):
        self.name = name
        self.cells: list[Cell] = []
        self.ids = {"0", "1"}
        self.abs: dict[str, tuple] = {"1": (0, 0, 0, 0)}  # absolute bounds of vertices
        self.warnings: list[str] = []
        # per-cell descriptive metadata for the interactive HTML emitter
        # (emit_html.py). Keyed by cell id. Shape: {spec_id, title, description,
        # technology, stereotype, kind, members: [str]}. Boxes without a spec
        # description still get an entry (title/kind) so the side panel can
        # always show at least a name and type.
        self.meta: dict[str, dict] = {}
        self.edge_meta: dict[str, dict] = {}
        # segments of connectors routed so far, so later ones steer around them
        self.routed: list[tuple] = []

    # ids ---------------------------------------------------------------
    def uid(self, base: str) -> str:
        base = re.sub(r"[^A-Za-z0-9_.:-]", "_", str(base)) or "cell"
        if base in ("0", "1"):
            base = f"c{base}"
        cand, i = base, 2
        while cand in self.ids:
            cand = f"{base}_{i}"
            i += 1
        self.ids.add(cand)
        return cand

    # vertices ----------------------------------------------------------
    def vertex(self, cid, value, style, x, y, w, h, parent="1", meta=None):
        cell = Cell(cid, "vertex", value, style, parent)
        cell.x, cell.y, cell.w, cell.h = x, y, w, h
        cell.relative = False
        self.cells.append(cell)
        px, py = self.abs.get(parent, (0, 0, 0, 0))[:2]
        self.abs[cid] = (px + x, py + y, w, h)
        if meta is not None:
            self.meta[cid] = meta
        return cid

    # edges -------------------------------------------------------------
    def edge(self, cid, value, style, source=None, target=None, parent="1",
             points=None, source_point=None, target_point=None, meta=None):
        cell = Cell(cid, "edge", value, style, parent)
        cell.source = source or None
        cell.target = target or None
        cell.points = list(points) if points else []
        cell.source_point = source_point
        cell.target_point = target_point
        self.cells.append(cell)
        if meta is not None:
            self.edge_meta[cid] = meta
        return cid

    def edge_label(self, edge_id, value, pos=-1.0, offset=(0, 0), style=None):
        """A text label attached to an edge. pos: -1 = source end, 0 = middle, 1 = target end."""
        st = style or ("edgeLabel;resizable=0;align=left;verticalAlign=top;"
                       "labelBackgroundColor=default;")
        cid = self.uid(f"{edge_id}_lbl")
        cell = Cell(cid, "vertex", value, st, edge_id)
        cell.relative = True       # positioned along its parent edge, not a box
        cell.x, cell.y = pos, 0.0  # pos encodes source/middle/target placement
        cell.points = [offset]     # label offset (reused slot)
        self.cells.append(cell)
        return cid

    def spread_parallel_edges(self):
        """Give edges that join the same two shapes distinct attachment points,
        so they don't draw on top of each other."""
        groups = {}
        for c in self.cells:
            if not c.is_edge:
                continue
            s, t = c.source, c.target
            if not s or not t or s == t or s not in self.abs or t not in self.abs:
                continue
            if "exitX" in c.style or "entityRelationEdgeStyle" in c.style:
                continue
            groups.setdefault(frozenset((s, t)), []).append(c)
        for cells in groups.values():
            if len(cells) < 2:
                continue
            n = len(cells)
            for k, c in enumerate(cells):
                f = round((k + 1) / (n + 1), 3)
                sx, sy, sw, sh = self.abs[c.source]
                tx, ty, tw, th = self.abs[c.target]
                dx = (tx + tw / 2) - (sx + sw / 2)
                dy = (ty + th / 2) - (sy + sh / 2)
                if abs(dx) >= abs(dy):
                    add = (f"exitX={1 if dx > 0 else 0};exitY={f};entryX={0 if dx > 0 else 1};entryY={f};")
                else:
                    add = (f"exitX={f};exitY={1 if dy > 0 else 0};entryX={f};entryY={0 if dy > 0 else 1};")
                c.style = merge_style(c.style, add + "exitDx=0;exitDy=0;entryDx=0;entryDy=0;")

    # obstacle-avoiding routing -----------------------------------------
    def route_edge(self, source, target, avoid_ids=None, margin=14, ports=None, strict=False):
        """Compute an explicit orthogonal route from `source` to `target` that
        steers clear of every other placed shape, so the edge never gets
        drawn on top of an unrelated box. Returns `(points, style_extra)`:
        `points` is ready to pass as `edge()`'s `points=`, and `style_extra`
        MUST be merged into that edge's style (it pins the exact spot on each
        shape's border the path assumed — see `_anchor_style`). Both are
        `None` when routing isn't possible for this pair (falls back to a
        plain auto-routed edge).

        `avoid_ids` lets the caller exclude cells that shouldn't count as
        obstacles for this particular edge (defaults to just source/target).
        Containers that are an ancestor of source or target, and shapes that
        are nested inside source or target, are always excluded automatically
        since a box can never block a line leaving/entering itself.
        """
        if source not in self.abs or target not in self.abs:
            return None, None
        sbox, tbox = self.abs[source], self.abs[target]
        exclude = {source, target} | set(avoid_ids or ())
        obstacles = []
        for cid, box in self.abs.items():
            if cid == "1" or cid in exclude:
                continue
            if _box_contains(box, sbox) or _box_contains(box, tbox):
                continue  # ancestor container of source/target
            if _box_contains(sbox, box) or _box_contains(tbox, box):
                continue  # shape nested inside source/target
            obstacles.append(box)
        pts, anchor = route_orthogonal(sbox, tbox, obstacles, margin=margin,
                                       existing=self.routed, ports=ports, strict=strict)
        if pts is not None:
            st = dict(parse_style(anchor))
            a = (sbox[0] + sbox[2] * float(st["exitX"]), sbox[1] + sbox[3] * float(st["exitY"]))
            b = (tbox[0] + tbox[2] * float(st["entryX"]), tbox[1] + tbox[3] * float(st["entryY"]))
            full = [a, *pts, b]
            self.routed.extend(zip(full, full[1:]))
        return pts, anchor

    def crossable_containers(self):
        """Containers whose job is to group other diagram elements (lanes,
        pools, composite states, system boundaries, packages). Connectors may
        cross them; only the elements inside are obstacles. A class box or ER
        table also holds child cells (its rows), but those aren't elements, so
        the box itself stays solid."""
        boxes = [(c.id, self.abs[c.id]) for c in self.cells
                 if c.is_vertex and not c.relative and c.id in self.abs]
        return {cid for cid, b in boxes
                if any(o != cid and o in self.meta and _box_contains(b, ob) for o, ob in boxes)}

    def route_unrouted(self, margin=14):
        """Route every box-to-box connector that has no route yet (flowchart,
        activity, state, class and use-case edges are created unrouted), so
        none is drawn as a straight line through another box or across
        another connector."""
        todo = [c for c in self.cells
                if c.is_edge and c.source and c.target and c.source != c.target
                and not c.points and "exitX" not in c.style
                and c.source in self.abs and c.target in self.abs]
        if not todo:
            return
        centred = {c.id for c in self.cells if c.is_vertex and any(
            k in c.style for k in ("rhombus", "ellipse", "umlActor", "startState", "endState"))}
        routes = self.route_edges([(c.source, c.target) for c in todo], margin,
                                  centred=centred, avoid=self.crossable_containers())
        for c, (pts, anchor) in zip(todo, routes):
            if pts is not None:
                c.points = pts
                c.style = merge_style(c.style, anchor)

    def route_edges(self, pairs, margin=14, centred=(), fixed=None, avoid=()):
        """Route many connectors at once. Where several connectors meet the
        same side of a shape, their attachment points are spread along that
        side in the order of the shapes at their other ends, so they fan out
        without crossing each other or sharing a line. Shapes in `centred`
        (diamonds, circles, actors) keep every attachment at the side's
        middle, where their outline actually is. `fixed` maps a pair's index
        to `((exit side, frac), (entry side, frac))` for connectors whose ends
        must sit at a given spot (e.g. an ER column row). Returns one
        `(points, style_extra)` per pair, like `route_edge`."""
        fixed = fixed or {}
        ends = []
        for s, t in pairs:
            if s not in self.abs or t not in self.abs or s == t:
                ends.append(None)
                continue
            sb, tb = self.abs[s], self.abs[t]
            sc = (sb[0] + sb[2] / 2, sb[1] + sb[3] / 2)
            tc = (tb[0] + tb[2] / 2, tb[1] + tb[3] / 2)
            ends.append(((s, _exit_side(sb, tc), tc), (t, _exit_side(tb, sc), sc)))
        # a diamond / circle has one attachment point per side: give each
        # connector its own side so lines in and out never run together
        by_side = defaultdict(list)
        for i, e in enumerate(ends):
            if e is not None and i not in fixed:
                for j, (cid, side, other) in enumerate(e):
                    if cid in centred:
                        by_side[cid].append((i, j))
        for cid, members in by_side.items():
            box = self.abs[cid]
            cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
            used = set()
            # incoming ends keep their natural side first (the flow arrives
            # from where it comes from), then outgoing ones pick what's left
            for i, j in sorted(members, key=lambda m: -m[1]):
                _c, side, other = ends[i][j]
                if side in used:
                    dx, dy = other[0] - cx, other[1] - cy
                    pref = sorted(("left", "right", "top", "bottom"), key=lambda k: -(
                        {"left": -dx, "right": dx, "top": -dy, "bottom": dy}[k]))
                    side = next((k for k in pref if k not in used), side)
                    e = list(ends[i])
                    e[j] = (cid, side, other)
                    ends[i] = tuple(e)
                used.add(side)
        slots = defaultdict(list)  # (cell, side) -> [(sort key, edge #, end #)]
        frac = {}
        for i, e in enumerate(ends):
            if e is None:
                continue
            if i in fixed:
                (ss, sf), (ts, tf) = fixed[i]
                ends[i] = ((e[0][0], ss, e[0][2]), (e[1][0], ts, e[1][2]))
                frac[(i, 0)], frac[(i, 1)] = sf, tf
                continue
            for j, (cid, side, other) in enumerate(e):
                key = other[0] if side in ("top", "bottom") else other[1]
                slots[(cid, side)].append((key, i, j))
        for (cid, _side), members in slots.items():
            members.sort()
            n = len(members)
            for k, (_key, i, j) in enumerate(members):
                frac[(i, j)] = 0.5 if cid in centred else (k + 1) / (n + 1)
        # short connectors first: they have the fewest ways around, so the
        # long ones should be the ones that detour
        order = sorted((i for i, e in enumerate(ends) if e is not None),
                       key=lambda i: _centre_dist(self.abs[pairs[i][0]], self.abs[pairs[i][1]]))
        out = [(None, None)] * len(pairs)
        segs = {}
        base = len(self.routed)
        for i in order:
            (s, ss, _), (t, ts, _) = ends[i]
            n0 = len(self.routed)
            out[i] = self.route_edge(s, t, margin=margin, avoid_ids=avoid, strict=i in fixed,
                                     ports=((ss, frac[(i, 0)]), (ts, frac[(i, 1)])))
            segs[i] = self.routed[n0:]
        # second pass: rip up each connector and reroute it against all the
        # others, so early connectors don't keep a route that forces crossings
        for i in order:
            if not segs[i]:
                continue
            others = [g for j in order if j != i for g in segs[j]]
            before = _path_cost([segs[i][0][0]] + [b for _a, b in segs[i]], others)
            del self.routed[base:]
            self.routed.extend(others)
            n0 = len(self.routed)
            (s, ss, _), (t, ts, _) = ends[i]
            cand = self.route_edge(s, t, margin=margin, avoid_ids=avoid, strict=i in fixed,
                                   ports=((ss, frac[(i, 0)]), (ts, frac[(i, 1)])))
            new = self.routed[n0:]
            if new and _path_cost([new[0][0]] + [b for _a, b in new], others) < before:
                out[i], segs[i] = cand, new
            del self.routed[base:]
            self.routed.extend(g for j in order for g in segs[j])
        return out

    # output ------------------------------------------------------------
    def bounds(self):
        xs = [(x, y, x + w, y + h) for k, (x, y, w, h) in self.abs.items() if k != "1"]
        if not xs:
            return (0, 0, 800, 600)
        return (min(a[0] for a in xs), min(a[1] for a in xs),
                max(a[2] for a in xs), max(a[3] for a in xs))


# ------------------------------------------------------------------------ layout

def _components(ids, edges):
    parent = {i: i for i in ids}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for u, v in edges:
        parent[find(u)] = find(v)
    groups = defaultdict(list)
    for i in ids:
        groups[find(i)].append(i)
    # keep input order of first appearance
    return sorted(groups.values(), key=lambda g: ids.index(g[0]))


def _break_cycles(ids, edges):
    """Return DAG edges (back edges reversed) using DFS in input order."""
    adj = defaultdict(list)
    for u, v in edges:
        adj[u].append(v)
    state = {}
    back = set()
    for s in ids:
        if s in state:
            continue
        stack = [(s, iter(adj[s]))]
        state[s] = 1
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                state[node] = 2
                stack.pop()
                continue
            st = state.get(nxt)
            if st == 1:
                back.add((node, nxt))
            elif st is None:
                state[nxt] = 1
                stack.append((nxt, iter(adj[nxt])))
    dag = []
    for u, v in edges:
        dag.append((v, u) if (u, v) in back else (u, v))
    return dag


def _rank(ids, dag):
    preds = defaultdict(list)
    succs = defaultdict(list)
    indeg = {i: 0 for i in ids}
    for u, v in dag:
        preds[v].append(u)
        succs[u].append(v)
        indeg[v] += 1
    q = deque([i for i in ids if indeg[i] == 0])
    order = []
    while q:
        n = q.popleft()
        order.append(n)
        for m in succs[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                q.append(m)
    rank = {}
    for n in order:
        rank[n] = max((rank[p] + 1 for p in preds[n]), default=0)
    # pull pure sources down next to their successors (shortens edges)
    for n in reversed(order):
        if not preds[n] and succs[n]:
            rank[n] = max(rank[n], min(rank[s] for s in succs[n]) - 1)
    return rank


def _crossings(layer_a, layer_b, edges_ab):
    pa = {n: i for i, n in enumerate(layer_a)}
    pb = {n: i for i, n in enumerate(layer_b)}
    es = [(pa[u], pb[v]) for u, v in edges_ab if u in pa and v in pb]
    c = 0
    for i in range(len(es)):
        for j in range(i + 1, len(es)):
            if (es[i][0] - es[j][0]) * (es[i][1] - es[j][1]) < 0:
                c += 1
    return c


def _isotonic(desired, sizes, gaps):
    """Place items in fixed order minimising squared distance to desired centres."""
    n = len(desired)
    if n == 0:
        return []
    off = [0.0] * n
    for i in range(1, n):
        off[i] = off[i - 1] + (sizes[i - 1] + sizes[i]) / 2 + gaps[i]
    t = [desired[i] - off[i] for i in range(n)]
    blocks = []  # [sum, count, start]
    for i, val in enumerate(t):
        blocks.append([val, 1, i])
        while len(blocks) > 1 and blocks[-2][0] / blocks[-2][1] > blocks[-1][0] / blocks[-1][1]:
            s, c, _ = blocks.pop()
            blocks[-1][0] += s
            blocks[-1][1] += c
    z = [0.0] * n
    for s, c, start in blocks:
        for k in range(start, start + c):
            z[k] = s / c
    return [z[i] + off[i] for i in range(n)]


def _layout_component(ids, sizes, edges, direction, node_gap, rank_gap, weak=()):
    """edges: strong edges (define the ranking); weak: edges that only attract."""
    horiz = direction in ("LR", "RL")
    along = (lambda n: sizes[n][1]) if horiz else (lambda n: sizes[n][0])
    across = (lambda n: sizes[n][0]) if horiz else (lambda n: sizes[n][1])

    weak = list(weak)
    if not edges:
        edges, weak = weak, []
    dag = _break_cycles(ids, edges)
    rank = _rank(ids, dag)
    touched = {n for e in dag for n in e}
    loose = [n for n in ids if n not in touched]
    if weak and loose and touched:
        placed = set(touched)
        for _ in range(len(loose)):
            progress = False
            for n in loose:
                if n in placed:
                    continue
                cands = [rank[v if u == n else u] + (-1 if u == n else 1) for u, v in weak
                         if n in (u, v) and (v if u == n else u) in placed]
                if cands:
                    rank[n] = round(sum(cands) / len(cands))
                    placed.add(n)
                    progress = True
            if not progress:
                break
        low = min(rank.values())
        rank = {n: r - low for n, r in rank.items()}
    for u, v in weak:
        if rank[u] < rank[v]:
            dag.append((u, v))
        elif rank[u] > rank[v]:
            dag.append((v, u))

    # dummy nodes for long edges (used for ordering and spacing)
    all_nodes = list(ids)
    is_dummy = set()
    seg_edges = []
    for u, v in dag:
        ru, rv = rank[u], rank[v]
        if rv - ru <= 1:
            seg_edges.append((u, v))
            continue
        prev = u
        for r in range(ru + 1, rv):
            d = f"__d{len(is_dummy)}"
            is_dummy.add(d)
            rank[d] = r
            all_nodes.append(d)
            seg_edges.append((prev, d))
            prev = d
        seg_edges.append((prev, v))

    nlayers = max(rank.values()) + 1
    layers = [[] for _ in range(nlayers)]
    # initial order: BFS from sources in input order
    succ = defaultdict(list)
    pred = defaultdict(list)
    for u, v in seg_edges:
        succ[u].append(v)
        pred[v].append(u)
    seen = set()
    for s in all_nodes:
        if s in seen:
            continue
        q = deque([s])
        seen.add(s)
        while q:
            n = q.popleft()
            layers[rank[n]].append(n)
            for m in succ[n] + pred[n]:
                if m not in seen:
                    seen.add(m)
                    q.append(m)

    def edges_between(r):
        return [(u, v) for u, v in seg_edges if rank[u] == r and rank[v] == r + 1]

    def total_cross(ls):
        return sum(_crossings(ls[r], ls[r + 1], edges_between(r)) for r in range(nlayers - 1))

    best = [l[:] for l in layers]
    best_c = total_cross(best)
    for it in range(16):
        down = it % 2 == 0
        rng = range(1, nlayers) if down else range(nlayers - 2, -1, -1)
        for r in rng:
            ref = layers[r - 1] if down else layers[r + 1]
            pos = {n: i for i, n in enumerate(ref)}
            cur = {n: i for i, n in enumerate(layers[r])}

            def bc(n):
                nb = [pos[m] for m in (pred[n] if down else succ[n]) if m in pos]
                return sum(nb) / len(nb) if nb else cur[n]
            layers[r].sort(key=lambda n: (bc(n), cur[n]))
        c = total_cross(layers)
        if c < best_c:
            best_c, best = c, [l[:] for l in layers]
        if best_c == 0:
            break
    layers = best

    # coordinate along the layer
    size_along = {n: (0 if n in is_dummy else along(n)) for n in all_nodes}
    centre = {}
    for layer in layers:
        x = 0.0
        for n in layer:
            centre[n] = x + size_along[n] / 2
            x += size_along[n] + node_gap

    def gaps_for(layer):
        g = [0.0]
        for i in range(1, len(layer)):
            dummy = layer[i] in is_dummy or layer[i - 1] in is_dummy
            g.append(node_gap / 2 if dummy else node_gap)
        return g

    for it in range(8):
        order = list(range(nlayers)) if it % 2 == 0 else list(range(nlayers - 1, -1, -1))
        for r in order:
            layer = layers[r]
            desired = []
            for n in layer:
                if it >= 6:
                    nb = pred[n] + succ[n]
                else:
                    nb = pred[n] if it % 2 == 0 else succ[n]
                vals = [centre[m] for m in nb]
                desired.append(sum(vals) / len(vals) if vals else centre[n])
            placed = _isotonic(desired, [size_along[n] for n in layer], gaps_for(layer))
            for n, c in zip(layer, placed):
                centre[n] = c

    # coordinate across layers
    band = [max((across(n) for n in l if n not in is_dummy), default=0) for l in layers]
    start = []
    acc = 0.0
    for b in band:
        start.append(acc)
        acc += b + rank_gap

    pos = {}
    for r, layer in enumerate(layers):
        for n in layer:
            if n in is_dummy:
                continue
            a = centre[n] - along(n) / 2
            b = start[r] + (band[r] - across(n)) / 2
            pos[n] = (b, a) if horiz else (a, b)
    minx = min(p[0] for p in pos.values())
    miny = min(p[1] for p in pos.values())
    pos = {n: (x - minx, y - miny) for n, (x, y) in pos.items()}
    if direction in ("BT", "RL"):
        extent = max((pos[n][1 if direction == "BT" else 0] +
                      sizes[n][1 if direction == "BT" else 0]) for n in pos)
        if direction == "BT":
            pos = {n: (x, extent - y - sizes[n][1]) for n, (x, y) in pos.items()}
        else:
            pos = {n: (extent - x - sizes[n][0], y) for n, (x, y) in pos.items()}
    w = max(pos[n][0] + sizes[n][0] for n in pos)
    h = max(pos[n][1] + sizes[n][1] for n in pos)
    return pos, (w, h)


def layered_layout(sizes: dict, edges, direction="TB", node_gap=40, rank_gap=70,
                   comp_gap=80, wrap=1600):
    """sizes: {id: (w, h)} (insertion order matters); edges: [(u, v)] or [(u, v, "weak")].
    Weak edges keep nodes close but do not force one rank below another.
    Returns ({id: (x, y)} top-left positions, (width, height))."""
    ids = list(sizes)
    if not ids:
        return {}, (0, 0)
    known = set(ids)
    es, weak_es = [], []
    seen = set()
    for e in edges:
        u, v = e[0], e[1]
        is_weak = len(e) > 2 and e[2] == "weak"
        if u in known and v in known and u != v and (u, v) not in seen:
            seen.add((u, v))
            (weak_es if is_weak else es).append((u, v))
    strong_pairs = set(es)
    weak_es = [e for e in weak_es if e not in strong_pairs and (e[1], e[0]) not in strong_pairs]
    comps = _components(ids, es + weak_es)
    # singletons are packed together at the end in a grid
    multi = [c for c in comps if len(c) > 1]
    singles = [c[0] for c in comps if len(c) == 1]
    blocks = []
    for comp in multi:
        cset = set(comp)
        ce = [(u, v) for u, v in es if u in cset]
        cw = [(u, v) for u, v in weak_es if u in cset]
        blocks.append(_layout_component(comp, sizes, ce, direction, node_gap, rank_gap, cw))
    if singles:
        per_row = max(1, min(len(singles), math.ceil(math.sqrt(len(singles) * 1.5))))
        pos, x, y, row_h, W = {}, 0.0, 0.0, 0.0, 0.0
        for i, n in enumerate(singles):
            if i and i % per_row == 0:
                x, y, row_h = 0.0, y + row_h + node_gap, 0.0
            pos[n] = (x, y)
            x += sizes[n][0] + node_gap
            W = max(W, x - node_gap)
            row_h = max(row_h, sizes[n][1])
        blocks.append((pos, (W, y + row_h)))

    horiz = direction in ("LR", "RL")
    out = {}
    cx = cy = line = 0.0
    tw = th = 0.0
    for pos, (w, h) in blocks:
        if horiz:  # stack blocks vertically
            if cy > 0 and cy + h > wrap:
                cx, cy = cx + line + comp_gap, 0.0
                line = 0.0
            for n, (x, y) in pos.items():
                out[n] = (cx + x, cy + y)
            cy += h + comp_gap
            line = max(line, w)
        else:  # place blocks side by side
            if cx > 0 and cx + w > wrap:
                cx, cy = 0.0, cy + line + comp_gap
                line = 0.0
            for n, (x, y) in pos.items():
                out[n] = (cx + x, cy + y)
            cx += w + comp_gap
            line = max(line, h)
    tw = max(out[n][0] + sizes[n][0] for n in out)
    th = max(out[n][1] + sizes[n][1] for n in out)
    return out, (tw, th)


def nested_layout(roots, children, sizes, edges, direction="TB", pad=20,
                  header=None, min_width=None, node_gap=40, rank_gap=70):
    """Lay out a containment tree.

    roots:     ordered list of top-level ids
    children:  {container_id: [child ids]}
    sizes:     {leaf_id: (w, h)}  (container sizes are filled in)
    header:    {container_id: header height}  (space for label at top)
    min_width: {container_id: minimum width}
    Returns (rel_pos {id: (x, y) relative to parent}, sizes, total (w, h)).
    """
    header = header or {}
    min_width = min_width or {}
    parent = {}
    for c, ks in children.items():
        for k in ks:
            parent[k] = c

    def chain(n):
        out = [n]
        while n in parent:
            n = parent[n]
            out.append(n)
        return out

    chains = {}
    rel = {}

    def project(kids):
        ks = set(kids)
        res = []
        for e in edges:
            u, v = e[0], e[1]
            cu = chains.setdefault(u, chain(u))
            cv = chains.setdefault(v, chain(v))
            a = next((x for x in cu if x in ks), None)
            b = next((x for x in cv if x in ks), None)
            if a and b and a != b:
                res.append((a, b) + tuple(e[2:]))
        return res

    def lay(cid, kids):
        for k in kids:
            if children.get(k):
                lay(k, children[k])
        sub = {k: sizes[k] for k in kids}
        pos, (w, h) = layered_layout(sub, project(kids), direction, node_gap, rank_gap)
        if cid is None:
            for k, p in pos.items():
                rel[k] = p
            return (w, h)
        top = header.get(cid, 30)
        cw = max(w + 2 * pad, min_width.get(cid, 0))
        shift = (cw - w) / 2
        for k, (x, y) in pos.items():
            rel[k] = (x + shift, y + top + pad / 2)
        sizes[cid] = (cw, h + top + pad)
        return sizes[cid]

    total = lay(None, roots)
    return rel, sizes, total


def lane_layout(order, sizes, edges, lane_of, lanes, node_gap=40, rank_gap=60,
                lane_pad=30, header=40):
    """Cross-functional (swimlane) layout: lanes are vertical columns, flow is top-down.
    Returns (rel_pos relative to lane, lane_x, lane_w, total_h)."""
    dag = _break_cycles(order, edges)
    rank = _rank(order, dag)
    nr = max(rank.values()) + 1 if rank else 1
    cells = defaultdict(list)
    for n in order:
        cells[(lane_of[n], rank[n])].append(n)
    lane_w = {}
    for ln in lanes:
        widths = [sum(sizes[n][0] for n in cells[(ln, r)]) +
                  node_gap * max(0, len(cells[(ln, r)]) - 1) for r in range(nr)]
        lane_w[ln] = max(max(widths, default=0) + 2 * lane_pad,
                         text_width(ln, bold=True) + 40, 160)
    band = [max((sizes[n][1] for ln in lanes for n in cells[(ln, r)]), default=0)
            for r in range(nr)]
    ystart, acc = [], header + lane_pad / 2
    for b in band:
        ystart.append(acc)
        acc += b + rank_gap
    total_h = acc - rank_gap + lane_pad
    lane_x, acc = {}, 0.0
    for ln in lanes:
        lane_x[ln] = acc
        acc += lane_w[ln]
    preds = defaultdict(list)
    for u, v in dag:
        preds[v].append(u)
    rel, absx = {}, {}
    for r in range(nr):
        for ln in lanes:
            ns = cells[(ln, r)]
            ns.sort(key=lambda n: (sum(absx.get(p, 0) for p in preds[n]) /
                                   max(1, len(preds[n]))))
            tot = sum(sizes[n][0] for n in ns) + node_gap * max(0, len(ns) - 1)
            x = (lane_w[ln] - tot) / 2
            for n in ns:
                y = ystart[r] + (band[r] - sizes[n][1]) / 2
                rel[n] = (x, y)
                absx[n] = lane_x[ln] + x + sizes[n][0] / 2
                x += sizes[n][0] + node_gap
    return rel, lane_x, lane_w, total_h
