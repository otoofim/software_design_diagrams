"""Geometry self-check for a laid-out Page: finds connectors a reader would
misread. Works on the exact polylines the HTML emitter draws, so what it
reports is what ends up on screen.

Checks:
- two connectors cross each other;
- a connector runs through a box that is neither of its ends;
- two connectors with no box in common share a stretch of line, so a reader
  can't tell which one continues where;
- an edge label sits on top of a box or another label.

Sequence diagrams are skipped: messages cross lifelines by design.
"""
from emit_html import _edge_geo, place_edge_labels
from layout_lib import _box_contains

#: intersections this close to a connector's own endpoint are where several
#: connectors meet the same box, not a crossing a reader would notice
_END_TOL = 4.0
#: shared stretches shorter than this read as a touch, not as one line
_MIN_OVERLAP = 12.0


def _name(page, cid):
    m = page.meta.get(cid) or {}
    return str(m.get("spec_id") or m.get("title") or cid)


def _edge_name(page, c):
    return f"{_name(page, c.source)} → {_name(page, c.target)}"


def _segments_cross(p1, p2, q1, q2):
    """Point where segment p1-p2 properly crosses q1-q2, or None. Collinear
    overlaps are handled separately by _overlap_len."""
    d1x, d1y = p2[0] - p1[0], p2[1] - p1[1]
    d2x, d2y = q2[0] - q1[0], q2[1] - q1[1]
    den = d1x * d2y - d1y * d2x
    if abs(den) < 1e-9:
        return None
    t = ((q1[0] - p1[0]) * d2y - (q1[1] - p1[1]) * d2x) / den
    u = ((q1[0] - p1[0]) * d1y - (q1[1] - p1[1]) * d1x) / den
    if 0 < t < 1 and 0 < u < 1:
        return (p1[0] + t * d1x, p1[1] + t * d1y)
    return None


def _overlap_len(p1, p2, q1, q2, tol=1.0):
    """Length two axis-aligned segments run on top of each other (0 if not)."""
    if abs(p1[0] - p2[0]) < tol and abs(q1[0] - q2[0]) < tol and abs(p1[0] - q1[0]) < tol:
        a, b = sorted((p1[1], p2[1])), sorted((q1[1], q2[1]))
    elif abs(p1[1] - p2[1]) < tol and abs(q1[1] - q2[1]) < tol and abs(p1[1] - q1[1]) < tol:
        a, b = sorted((p1[0], p2[0])), sorted((q1[0], q2[0]))
    else:
        return 0.0
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def _seg_through_box(p, q, box, pad=2.0):
    """Does segment p-q pass through the interior of `box` (shrunk by pad)?
    Sampled, so it also works for the diagonal connectors of layered layouts."""
    x0, y0 = box[0] + pad, box[1] + pad
    x1, y1 = box[0] + box[2] - pad, box[1] + box[3] - pad
    if x1 <= x0 or y1 <= y0:
        return False
    n = max(2, int((abs(q[0] - p[0]) + abs(q[1] - p[1])) / 3))
    for k in range(1, n):
        x = p[0] + (q[0] - p[0]) * k / n
        y = p[1] + (q[1] - p[1]) * k / n
        if x0 < x < x1 and y0 < y < y1:
            return True
    return False


def _near(pt, ends):
    return any(abs(pt[0] - e[0]) <= _END_TOL and abs(pt[1] - e[1]) <= _END_TOL for e in ends)


def _rects_overlap(a, b, slack=1.0):
    return (a[0] < b[0] + b[2] - slack and b[0] < a[0] + a[2] - slack and
            a[1] < b[1] + b[3] - slack and b[1] < a[1] + a[3] - slack)


def check_layout(page, dtype=None):
    """Return human-readable problems with the page's connectors."""
    if dtype == "sequence":
        return []
    boxes = {c.id: page.abs[c.id] for c in page.cells
             if c.is_vertex and not c.relative and c.id in page.abs}
    # containers (groups, lanes, pools) are meant to be crossed by connectors
    # between their members and neighbours; every other element blocks a line
    crossable = page.crossable_containers()
    leaves = {cid: b for cid, b in boxes.items() if cid in page.meta and cid not in crossable}

    edges = []
    for c in page.cells:
        if not c.is_edge or not c.source or not c.target:
            continue
        path, _st = _edge_geo(page, c)
        if path and len(path) >= 2:
            edges.append((c, path))

    probs = []
    for i, (c, path) in enumerate(edges):
        ends = (c.source, c.target)
        sb, tb = page.abs.get(c.source), page.abs.get(c.target)
        for cid, box in leaves.items():
            if cid in ends:
                continue
            if any(e and (_box_contains(box, e) or _box_contains(e, box)) for e in (sb, tb)):
                continue
            if any(_seg_through_box(p, q, box) for p, q in zip(path, path[1:])):
                probs.append(f"connector '{_edge_name(page, c)}' runs through "
                             f"'{_name(page, cid)}'")
        for d, other in edges[i + 1:]:
            all_ends = (path[0], path[-1], other[0], other[-1])
            shared = {c.source, c.target} & {d.source, d.target}
            # one line arriving where another leaves reads as a single line
            # passing through the box
            passing = bool({c.target} & {d.source} or {d.target} & {c.source})
            if passing and not ({c.source} & {d.source} or {c.target} & {d.target}):
                shared = set()
            crossed = overlap = False
            for p1, p2 in zip(path, path[1:]):
                for q1, q2 in zip(other, other[1:]):
                    pt = _segments_cross(p1, p2, q1, q2)
                    if pt and not _near(pt, all_ends):
                        crossed = True
                    if not shared and _overlap_len(p1, p2, q1, q2) >= _MIN_OVERLAP:
                        overlap = True
            if crossed:
                probs.append(f"connectors cross: '{_edge_name(page, c)}' and "
                             f"'{_edge_name(page, d)}'")
            if overlap:
                probs.append(f"connectors share a line: '{_edge_name(page, c)}' and "
                             f"'{_edge_name(page, d)}' -- a reader can't tell which goes where")

    by_id = {c.id: c for c, _p in edges}
    labels = [(by_id[eid], text, rect)
              for eid, (_x, _y, text, rect) in place_edge_labels(page).items() if eid in by_id]
    for i, (c, text, r) in enumerate(labels):
        for cid, box in leaves.items():
            if _rects_overlap(r, box):
                probs.append(f"label '{text.splitlines()[0]}' ({_edge_name(page, c)}) covers '{_name(page, cid)}'")
        for d, text2, r2 in labels[i + 1:]:
            if _rects_overlap(r, r2):
                probs.append(f"labels overlap: '{text}' ({_edge_name(page, c)}) and "
                             f"'{text2.splitlines()[0]}' ({_edge_name(page, d)})")
    return probs
