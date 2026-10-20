#!/usr/bin/env python3
"""
generate_diagrams.py - turn a design spec (JSON) into interactive HTML diagrams.

Usage:
  python generate_diagrams.py spec.json --out-dir OUT [--index OUT/index.html] [--only class,er]

Spec format: see references/spec-format.md. Top level:
  {"title": "...", "diagrams": [ {"type": "...", "name": "...", ...}, ... ]}

Supported types:
  sequence, flowchart, er, class, architecture, usecase, activity, state,
  component, deployment

Each diagram becomes its own self-contained, dependency-free interactive .html
file (click any component to see its description), plus an index.html linking
them all.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from layout_lib import (Page, colors, esc, fit_box, lane_layout, layered_layout,  # noqa: E402
                        merge_style, nested_layout, slug, text_width)
from layout_check import check_layout  # noqa: E402
from emit_html import write_html, write_index  # noqa: E402

MARGIN = 40
EDGE = ("edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;"
        "labelBackgroundColor=default;")


class SpecError(Exception):
    pass


def _need(d, key, where):
    if key not in d:
        raise SpecError(f"{where}: missing required field '{key}'")
    return d[key]


def _ids(items, where):
    out = {}
    for it in items:
        i = str(_need(it, "id", where))
        if i in out:
            raise SpecError(f"{where}: duplicate id '{i}'")
        out[i] = it
    return out


def _check_ref(ref, known, where, page):
    if str(ref) not in known:
        page.warnings.append(f"{where}: unknown id '{ref}' (known: {', '.join(sorted(known))[:300]}) - skipped")
        return False
    return True


# =============================================================== FLOWCHART
FLOW_SHAPES = {
    "start": ("rounded=1;whiteSpace=wrap;html=1;arcSize=50;" + colors("green"), 120, 40),
    "end": ("rounded=1;whiteSpace=wrap;html=1;arcSize=50;" + colors("red"), 120, 40),
    "process": ("rounded=0;whiteSpace=wrap;html=1;" + colors("blue"), 140, 60),
    "decision": ("rhombus;whiteSpace=wrap;html=1;" + colors("yellow"), 150, 90),
    "io": ("shape=parallelogram;perimeter=parallelogramPerimeter;whiteSpace=wrap;html=1;"
           "fixedSize=1;size=15;" + colors("purple"), 150, 60),
    "document": ("shape=document;whiteSpace=wrap;html=1;boundedLbl=1;" + colors("orange"), 140, 70),
    "database": ("shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;"
                 "size=12;" + colors("gray"), 120, 70),
    "subprocess": ("shape=process;whiteSpace=wrap;html=1;backgroundOutline=1;size=0.08;"
                   + colors("blue"), 160, 60),
    "manual": ("shape=manualInput;whiteSpace=wrap;html=1;size=12;" + colors("purple"), 140, 60),
    "delay": ("shape=delay;whiteSpace=wrap;html=1;" + colors("orange"), 120, 50),
    "connector": ("ellipse;whiteSpace=wrap;html=1;aspect=fixed;" + colors("gray"), 40, 40),
    "note": ("shape=note;whiteSpace=wrap;html=1;size=12;align=left;spacingLeft=6;"
             "fillColor=#fff2cc;strokeColor=#d6b656;dashed=1;", 160, 60),
}


def render_flowchart(d, page):
    nodes = d.get("nodes", [])
    known = _ids(nodes, f"{page.name}.nodes")
    direction = d.get("direction", "TB")
    sizes = {}
    for n in nodes:
        kind = n.get("kind", "process")
        if kind not in FLOW_SHAPES:
            page.warnings.append(f"node {n['id']}: unknown kind '{kind}', using process")
            kind = "process"
        _, mw, mh = FLOW_SHAPES[kind]
        label = n.get("label", n["id"])
        if kind == "decision":
            w, h = fit_box(label, mw, mh, 260, pad_w=70, pad_h=45)
        elif kind == "connector":
            w, h = mw, mh
        else:
            w, h = fit_box(label, mw, mh, 240)
        sizes[str(n["id"])] = (w, h)
    edges = [e for e in d.get("edges", [])
             if _check_ref(e.get("from"), known, "edge", page) and _check_ref(e.get("to"), known, "edge", page)]
    pairs = [(str(e["from"]), str(e["to"])) for e in edges]
    has_labels = any(e.get("label") for e in edges)
    lanes = d.get("lanes")
    cell_id = {}
    if lanes:
        lane_names = [l if isinstance(l, str) else l["name"] for l in lanes]
        lane_of = {}
        for n in nodes:
            ln = n.get("lane", lane_names[0])
            if ln not in lane_names:
                page.warnings.append(f"node {n['id']}: unknown lane '{ln}'")
                ln = lane_names[0]
            lane_of[str(n["id"])] = ln
        rel, lane_x, lane_w, total_h = lane_layout(
            [str(n["id"]) for n in nodes], sizes, pairs, lane_of, lane_names,
            rank_gap=_label_gap([e.get("label") for e in edges], "TB", 70 if has_labels else 50))
        pool_w = sum(lane_w.values())
        pool = page.vertex(page.uid("pool"), f"<b>{esc(d.get('name', 'Process'))}</b>",
                           "swimlane;childLayout=stackLayout;resizeParent=1;resizeParentMax=0;"
                           "horizontal=1;startSize=30;horizontalStack=1;html=1;collapsible=0;"
                           "fillColor=#f5f5f5;strokeColor=#666666;",
                           MARGIN, MARGIN, pool_w, total_h + 30)
        lane_ids = {}
        for i, ln in enumerate(lane_names):
            lane_ids[ln] = page.vertex(
                page.uid(f"lane_{slug(ln)}"), esc(ln),
                "swimlane;startSize=40;html=1;collapsible=0;horizontal=1;swimlaneLine=1;"
                "fillColor=" + ("#ffffff" if i % 2 == 0 else "#fafafa") + ";",
                lane_x[ln], 30, lane_w[ln], total_h, parent=pool)
        for n in nodes:
            nid = str(n["id"])
            kind = n.get("kind", "process") if n.get("kind", "process") in FLOW_SHAPES else "process"
            st = merge_style(FLOW_SHAPES[kind][0], n.get("style"))
            x, y = rel[nid]
            cell_id[nid] = page.vertex(page.uid(nid), esc(n.get("label", nid)), st, x, y,
                                       *sizes[nid], parent=lane_ids[lane_of[nid]],
                                       meta={"spec_id": nid, "title": n.get("label", nid),
                                             "description": n.get("description"), "kind": kind})
    else:
        pos, _ = layered_layout(sizes, pairs, direction, node_gap=50,
                                rank_gap=_label_gap([e.get("label") for e in edges], direction,
                                                    80 if has_labels else 60))
        for n in nodes:
            nid = str(n["id"])
            kind = n.get("kind", "process") if n.get("kind", "process") in FLOW_SHAPES else "process"
            st = merge_style(FLOW_SHAPES[kind][0], n.get("style"))
            x, y = pos[nid]
            cell_id[nid] = page.vertex(page.uid(nid), esc(n.get("label", nid)), st,
                                       MARGIN + x, MARGIN + y, *sizes[nid],
                                       meta={"spec_id": nid, "title": n.get("label", nid),
                                             "description": n.get("description"), "kind": kind})
    for i, e in enumerate(edges):
        st = EDGE + "endArrow=block;endFill=1;"
        if e.get("dashed"):
            st += "dashed=1;"
        page.edge(page.uid(f"e{i}"), esc(e.get("label", "")), merge_style(st, e.get("style")),
                  cell_id[str(e["from"])], cell_id[str(e["to"])])


# ================================================================ ACTIVITY
def _activity_shape(kind, label):
    if kind == "initial":
        return "ellipse;html=1;shape=startState;fillColor=#000000;strokeColor=#000000;", 30, 30, ""
    if kind == "final":
        return "ellipse;html=1;shape=endState;fillColor=#000000;strokeColor=#000000;", 30, 30, ""
    if kind == "flow_final":
        return "shape=sumEllipse;perimeter=ellipsePerimeter;whiteSpace=wrap;html=1;backgroundOutline=1;", 30, 30, ""
    if kind in ("fork", "join"):
        return "shape=line;html=1;strokeWidth=6;strokeColor=#000000;", 160, 10, ""
    if kind in ("decision", "merge"):
        if label:
            w, h = fit_box(label, 120, 70, 220, pad_w=60, pad_h=40)
            return "rhombus;whiteSpace=wrap;html=1;" + colors("yellow"), w, h, label
        return "rhombus;html=1;" + colors("yellow"), 40, 40, ""
    if kind == "object":
        w, h = fit_box(label, 120, 40, 220)
        return "rounded=0;whiteSpace=wrap;html=1;" + colors("gray"), w, h, label
    if kind == "send_signal":
        w, h = fit_box(label, 140, 50, 220, pad_w=40)
        return ("shape=step;perimeter=stepPerimeter;whiteSpace=wrap;html=1;fixedSize=1;size=15;"
                + colors("orange")), w, h, label
    if kind == "accept_event":
        w, h = fit_box(label, 140, 50, 220, pad_w=40)
        return ("shape=offPageConnector;whiteSpace=wrap;html=1;size=0.25;direction=west;"
                + colors("orange")), w, h, label
    if kind == "note":
        w, h = fit_box(label, 140, 50, 220)
        return FLOW_SHAPES["note"][0], w, h, label
    # action (default)
    w, h = fit_box(label, 130, 50, 240)
    return "rounded=1;whiteSpace=wrap;html=1;arcSize=40;" + colors("blue"), w, h, label


def render_activity(d, page):
    nodes = d.get("nodes", [])
    known = _ids(nodes, f"{page.name}.nodes")
    edges = [e for e in d.get("edges", [])
             if _check_ref(e.get("from"), known, "edge", page) and _check_ref(e.get("to"), known, "edge", page)]
    pairs = [(str(e["from"]), str(e["to"])) for e in edges]
    shapes, sizes = {}, {}
    fan = {}
    for u, v in pairs:
        fan[u] = fan.get(u, 0) + 1
        fan[v] = fan.get(v, 0) + 1
    for n in nodes:
        nid = str(n["id"])
        st, w, h, lbl = _activity_shape(n.get("kind", "action"), n.get("label", ""))
        if n.get("kind") in ("fork", "join"):
            w = max(w, 80 * fan.get(nid, 2))
        shapes[nid] = (merge_style(st, n.get("style")), lbl)
        sizes[nid] = (w, h)
    lanes = d.get("lanes") or d.get("partitions")
    has_labels = any(e.get("guard") or e.get("label") for e in edges)

    def _act_meta(n):
        # control nodes (fork/join/initial/final/merge/decision) with no text
        # don't earn a side-panel entry
        if n.get("kind") in ("fork", "join", "initial", "final", "merge") and not (
                n.get("label") or n.get("description")):
            return None
        return {"spec_id": str(n["id"]), "title": n.get("label", str(n["id"])),
                "description": n.get("description"), "kind": n.get("kind", "action")}

    ids = {}
    if lanes:
        names = [l if isinstance(l, str) else l["name"] for l in lanes]
        lane_of = {str(n["id"]): (n.get("lane") if n.get("lane") in names else names[0]) for n in nodes}
        rel, lx, lw, th = lane_layout([str(n["id"]) for n in nodes], sizes, pairs, lane_of, names,
                                      rank_gap=60 if has_labels else 45)
        lane_cells = {}
        for ln in names:
            lane_cells[ln] = page.vertex(page.uid(f"partition_{slug(ln)}"), f"<b>{esc(ln)}</b>",
                                         "swimlane;startSize=40;html=1;collapsible=0;"
                                         "fillColor=#f5f5f5;strokeColor=#666666;swimlaneFillColor=#ffffff;",
                                         MARGIN + lx[ln], MARGIN, lw[ln], th)
        for n in nodes:
            nid = str(n["id"])
            ids[nid] = page.vertex(page.uid(nid), esc(shapes[nid][1]), shapes[nid][0],
                                   *rel[nid], *sizes[nid], parent=lane_cells[lane_of[nid]],
                                   meta=_act_meta(n))
    else:
        pos, _ = layered_layout(sizes, pairs, d.get("direction", "TB"), node_gap=50,
                                rank_gap=65 if has_labels else 45)
        for n in nodes:
            nid = str(n["id"])
            ids[nid] = page.vertex(page.uid(nid), esc(shapes[nid][1]), shapes[nid][0],
                                   MARGIN + pos[nid][0], MARGIN + pos[nid][1], *sizes[nid],
                                   meta=_act_meta(n))
    for i, e in enumerate(edges):
        label = e.get("label", "")
        if e.get("guard"):
            label = f"[{e['guard']}]" + (f" {label}" if label else "")
        st = EDGE + "endArrow=open;endFill=0;endSize=10;"
        page.edge(page.uid(f"flow{i}"), esc(label), merge_style(st, e.get("style")),
                  ids[str(e["from"])], ids[str(e["to"])])


# ================================================================== STATE
def render_state(d, page):
    states = d.get("states", [])
    flat, children, roots = {}, {}, []

    def walk(items, parent):
        for s in items:
            sid = str(_need(s, "id", "state"))
            if sid in flat:
                raise SpecError(f"duplicate state id '{sid}'")
            flat[sid] = s
            (children.setdefault(parent, []) if parent else roots).append(sid)
            if s.get("children"):
                walk(s["children"], sid)
    walk(states, None)
    sizes, header, minw, label_of, style_of = {}, {}, {}, {}, {}
    meta_of = {}  # real states only; pseudostates get no side-panel entry
    for sid, s in flat.items():
        kind = s.get("kind", "composite" if s.get("children") else "state")
        name = s.get("label", sid)
        if kind in ("state", "composite"):
            _acts = [f"{k} / {s[k]}" for k in ("entry", "do", "exit") if s.get(k)]
            _acts += [str(a) for a in s.get("activities", [])]
            meta_of[sid] = {"spec_id": s.get("id", sid), "title": name,
                            "description": s.get("description"), "kind": kind,
                            "members": _acts}
        if kind == "initial":
            style_of[sid] = "ellipse;html=1;shape=startState;fillColor=#000000;strokeColor=#000000;"
            sizes[sid], label_of[sid] = (30, 30), ""
        elif kind == "final":
            style_of[sid] = "ellipse;html=1;shape=endState;fillColor=#000000;strokeColor=#000000;"
            sizes[sid], label_of[sid] = (30, 30), ""
        elif kind == "choice":
            style_of[sid] = "rhombus;html=1;" + colors("yellow")
            sizes[sid], label_of[sid] = (40, 40), ""
        elif kind in ("history", "deep_history"):
            style_of[sid] = "ellipse;html=1;fontStyle=1;" + colors("white")
            sizes[sid], label_of[sid] = (30, 30), "H*" if kind == "deep_history" else "H"
        elif kind in ("fork", "join"):
            style_of[sid] = "shape=line;html=1;strokeWidth=6;strokeColor=#000000;"
            sizes[sid], label_of[sid] = (100, 10), ""
        else:
            acts = []
            for key in ("entry", "do", "exit"):
                if s.get(key):
                    acts.append(f"{key} / {s[key]}")
            for a in s.get("activities", []):
                acts.append(str(a))
            if kind == "composite":
                style_of[sid] = ("rounded=1;arcSize=6;whiteSpace=wrap;html=1;verticalAlign=top;"
                                 "container=1;collapsible=0;" + colors("blue"))
                lbl = f"<b>{esc(name)}</b>"
                if acts:
                    lbl += "<br><font style='font-size:10px'>" + "<br>".join(esc(a) for a in acts) + "</font>"
                label_of[sid] = lbl
                header[sid] = 30 + 14 * len(acts)
                minw[sid] = max(text_width(name, bold=True), *(text_width(a, 10) for a in acts or [""])) + 40
            else:
                style_of[sid] = "rounded=1;arcSize=25;whiteSpace=wrap;html=1;" + colors("blue")
                if acts:
                    style_of[sid] += "verticalAlign=top;align=center;"
                    label_of[sid] = (f"<b>{esc(name)}</b><hr size='1'>"
                                     f"<div style='text-align:left'>{'<br>'.join(esc(a) for a in acts)}</div>")
                    w = max(text_width(name, bold=True), *(text_width(a) for a in acts)) + 30
                    sizes[sid] = (max(130, min(280, w)), 36 + 17 * len(acts) + 10)
                else:
                    label_of[sid] = f"<b>{esc(name)}</b>"
                    sizes[sid] = fit_box(name, 120, 50, 220, bold=True)
        style_of[sid] = merge_style(style_of[sid], s.get("style"))
    trans = [t for t in d.get("transitions", [])
             if _check_ref(t.get("from"), flat, "transition", page) and _check_ref(t.get("to"), flat, "transition", page)]
    pairs = [(str(t["from"]), str(t["to"])) for t in trans]
    tlabels = [" ".join(filter(None, (str(t.get("event", "")), t.get("guard") and f"[{t['guard']}]",
                                      t.get("action") and f"/ {t['action']}"))) for t in trans]
    rel, sizes, _ = nested_layout(roots, children, sizes, pairs, d.get("direction", "LR"),
                                  pad=30, header=header, min_width=minw, node_gap=50,
                                  rank_gap=_label_gap(tlabels, d.get("direction", "LR"), 110))
    cell = {}

    def place(items, parent_cell):
        for sid in items:
            x, y = rel[sid]
            if parent_cell == "1":
                x, y = x + MARGIN, y + MARGIN
            cell[sid] = page.vertex(page.uid(sid), label_of[sid], style_of[sid], x, y,
                                    *sizes[sid], parent=parent_cell, meta=meta_of.get(sid))
            if children.get(sid):
                place(children[sid], cell[sid])
    place(roots, "1")
    for i, t in enumerate(trans):
        lbl = str(t.get("event", ""))
        if t.get("guard"):
            lbl += f" [{t['guard']}]"
        if t.get("action"):
            lbl += f" / {t['action']}"
        st = EDGE + "endArrow=open;endFill=0;endSize=10;curved=0;"
        page.edge(page.uid(f"t{i}"), esc(lbl.strip()), merge_style(st, t.get("style")),
                  cell[str(t["from"])], cell[str(t["to"])])


# ===================================================================== ER
ER_ENDS = {
    "1": "ERmandOne", "1..1": "ERmandOne", "one": "ERmandOne",
    "0..1": "ERzeroToOne", "zero_or_one": "ERzeroToOne",
    "1..*": "ERoneToMany", "1..n": "ERoneToMany", "one_or_many": "ERoneToMany",
    "0..*": "ERzeroToMany", "0..n": "ERzeroToMany", "zero_or_many": "ERzeroToMany",
    "*": "ERmany", "n": "ERmany", "many": "ERmany", "m": "ERmany",
}
ROW_H = 26


def render_er(d, page):
    ents = d.get("entities", [])
    known = {}
    for e in ents:
        name = str(e.get("id") or _need(e, "name", "entity"))
        if name in known:
            raise SpecError(f"duplicate entity '{name}'")
        known[name] = e
    sizes = {}
    for name, e in known.items():
        rows = [_er_row_text(a) for a in e.get("attributes", [])]
        w = max([text_width(e.get("label", name), 13, True) + 40] +
                [text_width(r) + 30 for r in rows] + [180])
        sizes[name] = (min(360, w), 30 + ROW_H * max(1, len(rows)) + 4)
    rels = [r for r in d.get("relationships", [])
            if _check_ref(r.get("from"), known, "relationship", page) and _check_ref(r.get("to"), known, "relationship", page)]
    pairs = [(str(r["from"]), str(r["to"])) for r in rels]
    pos, _ = layered_layout(sizes, pairs, d.get("direction", "LR"), node_gap=50, rank_gap=140)
    cell, row_cell = {}, {}
    for name, e in known.items():
        x, y = pos[name]
        w, h = sizes[name]
        cid = page.vertex(
            page.uid(f"tbl_{name}"), esc(e.get("label", name)),
            merge_style("swimlane;fontStyle=1;childLayout=stackLayout;horizontal=1;startSize=30;"
                        "horizontalStack=0;resizeParent=1;resizeParentMax=0;resizeLast=0;"
                        "collapsible=1;marginBottom=0;whiteSpace=wrap;html=1;fontSize=13;rounded=0;"
                        + colors("blue"), e.get("style")),
            MARGIN + x, MARGIN + y, w, h,
            meta={"spec_id": e.get("id", name), "title": e.get("label", name),
                  "description": e.get("description"), "kind": "table",
                  "members": [_er_row_text(a) for a in e.get("attributes", [])]})
        cell[name] = cid
        for i, a in enumerate(e.get("attributes", [])):
            a = _norm_attr(a)
            rid = page.vertex(
                page.uid(f"{name}_{a['name']}"), _er_row_html(a),
                "text;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;"
                "spacingLeft=8;spacingRight=4;overflow=hidden;points=[[0,0.5],[1,0.5]];"
                "portConstraint=eastwest;rotatable=0;whiteSpace=wrap;html=1;",
                0, 30 + i * ROW_H, w, ROW_H, parent=cid)
            row_cell[(name, a["name"])] = rid
    er_edges, ports = [], []
    for i, r in enumerate(rels):
        f, t = str(r["from"]), str(r["to"])
        card = str(r.get("cardinality", "")).lower().replace(" ", "")
        fc, tc = r.get("from_card"), r.get("to_card")
        if card and not (fc or tc):
            sides = card.split(":")
            if len(sides) == 2:
                many = {"n": "0..*", "m": "0..*", "*": "0..*", "many": "0..*"}
                fc, tc = (many.get(x, x) for x in sides)
                for x in (fc, tc):
                    if x not in ER_ENDS:
                        page.warnings.append(f"relationship {f}->{t}: unknown cardinality '{x}'")
            else:
                page.warnings.append(f"relationship {f}->{t}: cardinality '{card}' should look like '1:N'")
                fc, tc = "1", "0..*"
        fc, tc = str(fc or "1"), str(tc or "0..*")
        src, dst = cell[f], cell[t]
        st = EDGE.replace("rounded=1", "rounded=0")
        st += (f"startArrow={ER_ENDS.get(fc.lower(), 'ERmandOne')};"
               f"endArrow={ER_ENDS.get(tc.lower(), 'ERzeroToMany')};startFill=0;endFill=0;"
               "startSize=12;endSize=12;labelBackgroundColor=default;")
        if r.get("identifying") is False or r.get("dashed"):
            st += "dashed=1;"
        ports.append(_er_ports(page, src, dst, row_cell.get((f, r.get("from_field"))),
                               row_cell.get((t, r.get("to_field")))))
        er_edges.append((page.uid(f"rel{i}"), esc(r.get("label", "")),
                         merge_style(st, r.get("style")), src, dst))
    # field-level relationships leave and enter at their column's row, from the
    # table side facing the other table; every route avoids the other tables
    routes = page.route_edges([(e[3], e[4]) for e in er_edges],
                              fixed={i: p for i, p in enumerate(ports) if p})
    for (eid, lbl, st, src, dst), (pts, anchor) in zip(er_edges, routes):
        page.edge(eid, lbl, merge_style(st, anchor) if anchor else st, src, dst, points=pts)


def _er_ports(page, src, dst, srow, drow):
    """Fixed (side, fraction) ports for a relationship drawn between two
    columns, or None for a table-level relationship."""
    if not srow and not drow:
        return None
    sb, db = page.abs[src], page.abs[dst]
    scx, dcx = sb[0] + sb[2] / 2, db[0] + db[2] / 2
    if sb[0] + sb[2] <= db[0]:
        sides = ("right", "left")
    elif db[0] + db[2] <= sb[0]:
        sides = ("left", "right")
    else:  # stacked: loop round the side they share
        sides = ("right", "right") if scx <= dcx else ("left", "left")

    def frac(table, row):
        if not row:
            return 0.5
        tb, rb = page.abs[table], page.abs[row]
        return (rb[1] + rb[3] / 2 - tb[1]) / tb[3]
    return (sides[0], frac(src, srow)), (sides[1], frac(dst, drow))


def _norm_attr(a):
    if isinstance(a, str):
        parts = a.split(":", 1)
        return {"name": parts[0].strip(), "type": parts[1].strip() if len(parts) > 1 else ""}
    return a


def _er_row_text(a):
    a = _norm_attr(a)
    keys = "/".join(k for k in ("PK", "FK", "UK") if a.get(k.lower()) or (k == "UK" and a.get("unique")))
    nn = "" if a.get("nullable", not a.get("pk")) else " NOT NULL"
    return f"{keys or '    '}   {a['name']} : {a.get('type', '')}{nn}"


def _er_row_html(a):
    keys = [k for k in ("pk", "fk") if a.get(k)]
    if a.get("unique") or a.get("uk"):
        keys.append("uk")
    tag = "<b>" + "/".join(k.upper() for k in keys) + "</b>&nbsp;&nbsp;" if keys else "&nbsp;" * 6
    name = esc(a["name"])
    if a.get("pk"):
        name = f"<u>{name}</u>"
    typ = esc(a.get("type", ""))
    nn = "" if a.get("nullable", not a.get("pk")) else " <font color='#b85450'>NOT NULL</font>"
    return f"{tag}{name} : <i>{typ}</i>{nn}"


# ================================================================== CLASS
VIS = {"public": "+", "private": "-", "protected": "#", "package": "~", "internal": "~"}
REL_STYLE = {
    "inheritance": "endArrow=block;endFill=0;endSize=14;",
    "extends": "endArrow=block;endFill=0;endSize=14;",
    "implementation": "endArrow=block;endFill=0;endSize=14;dashed=1;",
    "implements": "endArrow=block;endFill=0;endSize=14;dashed=1;",
    "association": "endArrow=open;endFill=0;endSize=10;",
    "bidirectional": "endArrow=none;",
    "aggregation": "startArrow=diamondThin;startFill=0;startSize=16;endArrow=open;endFill=0;endSize=10;",
    "composition": "startArrow=diamondThin;startFill=1;startSize=16;endArrow=open;endFill=0;endSize=10;",
    "dependency": "endArrow=open;endFill=0;endSize=10;dashed=1;",
    "realization": "endArrow=block;endFill=0;endSize=14;dashed=1;",
}


def _member_text(m, is_method):
    if isinstance(m, str):
        return m, {}
    vis = VIS.get(m.get("visibility", "public"), m.get("visibility", "+"))
    if is_method:
        params = m.get("params", [])
        if isinstance(params, list):
            params = ", ".join(p if isinstance(p, str) else
                               f"{p['name']}{': ' + p['type'] if p.get('type') else ''}" for p in params)
        txt = f"{vis} {m['name']}({params})"
        if m.get("returns"):
            txt += f": {m['returns']}"
    else:
        txt = f"{vis} {m['name']}"
        if m.get("type"):
            txt += f": {m['type']}"
        if m.get("default") is not None:
            txt += f" = {m['default']}"
    return txt, m


def _member_html(m, is_method):
    txt, meta = _member_text(m, is_method)
    h = esc(txt)
    if meta.get("static"):
        h = f"<u>{h}</u>"
    if meta.get("abstract"):
        h = f"<i>{h}</i>"
    return h


def render_class(d, page):
    classes = d.get("classes", [])
    known = {}
    for c in classes:
        cid = str(c.get("id") or _need(c, "name", "class"))
        if cid in known:
            raise SpecError(f"duplicate class '{cid}'")
        known[cid] = c
    info, sizes = {}, {}
    for cid, c in known.items():
        stereo = c.get("stereotype") or c.get("kind")
        if stereo in (None, "class"):
            stereo = None
        attrs = c.get("attributes", [])
        if stereo == "enum" and c.get("values"):
            attrs = list(c["values"])
        methods = c.get("methods", [])
        name = c.get("label") or c.get("name") or cid
        head = 26 if not stereo else 42
        a_txt = [_member_text(a, False)[0] for a in attrs]
        m_txt = [_member_text(m, True)[0] for m in methods]
        w = max([text_width(name, 12, True) + 40, text_width(f"«{stereo}»") + 40 if stereo else 0, 160] +
                [text_width(t) + 24 for t in a_txt + m_txt])
        w = min(w, 420)
        a_h = max(1, len(attrs)) * 18 + 8
        m_h = max(1, len(methods)) * 18 + 8 if (methods or stereo != "enum") else 0
        show_methods = bool(methods) or stereo not in ("enum",)
        h = head + a_h + (8 + m_h if show_methods else 0)
        sizes[cid] = (w, h)
        info[cid] = (name, stereo, attrs, methods, head, a_h, m_h, show_methods)
        known[cid]["_members"] = a_txt + m_txt
    rels = [r for r in d.get("relationships", [])
            if _check_ref(r.get("from"), known, "relationship", page) and _check_ref(r.get("to"), known, "relationship", page)]
    layer_edges = []
    for r in rels:
        kind = r.get("kind", "association")
        f, t = str(r["from"]), str(r["to"])
        if kind in ("inheritance", "extends", "implementation", "implements", "realization"):
            layer_edges.append((t, f))  # parent above child
        elif kind in ("composition", "aggregation"):
            layer_edges.append((f, t))  # whole above part
        else:
            layer_edges.append((f, t, "weak"))  # associations only pull classes together
    pos, _ = layered_layout(sizes, layer_edges, d.get("direction", "TB"), node_gap=60, rank_gap=90)
    cell = {}
    for cid in known:
        name, stereo, attrs, methods, head, a_h, m_h, show_m = info[cid]
        w, h = sizes[cid]
        label = f"<b>{esc(name)}</b>"
        if stereo == "abstract":
            label = f"<i><b>{esc(name)}</b></i>"
            stereo_txt = "«abstract»"
        else:
            stereo_txt = f"«{esc(stereo)}»" if stereo else ""
        if stereo_txt:
            label = f"{stereo_txt}<br>{label}"
        fill = {"interface": "green", "abstract": "purple", "enum": "yellow"}.get(stereo, "blue")
        x, y = pos[cid]
        box = page.vertex(
            page.uid(f"cls_{cid}"), label,
            merge_style("swimlane;fontStyle=0;align=center;verticalAlign=top;childLayout=stackLayout;"
                        f"horizontal=1;startSize={head};horizontalStack=0;resizeParent=1;resizeParentMax=0;"
                        "resizeLast=0;collapsible=1;marginBottom=0;whiteSpace=wrap;html=1;" + colors(fill),
                        known[cid].get("style")),
            MARGIN + x, MARGIN + y, w, h,
            meta={"spec_id": known[cid].get("id", cid), "title": name,
                  "description": known[cid].get("description"),
                  "stereotype": stereo, "kind": "class",
                  "members": known[cid].get("_members", [])})
        cell[cid] = box
        member_style = ("text;strokeColor=none;fillColor=none;align=left;verticalAlign=top;spacingLeft=6;"
                        "spacingRight=4;overflow=hidden;rotatable=0;points=[[0,0.5],[1,0.5]];"
                        "portConstraint=eastwest;whiteSpace=wrap;html=1;")
        page.vertex(page.uid(f"{cid}_attrs"),
                    "<br>".join(_member_html(a, False) for a in attrs), member_style, 0, head, w, a_h, parent=box)
        if show_m:
            page.vertex(page.uid(f"{cid}_sep"), "",
                        "line;strokeWidth=1;fillColor=none;align=left;verticalAlign=middle;spacingTop=-1;"
                        "spacingLeft=3;spacingRight=3;rotatable=0;labelPosition=right;points=[];"
                        "portConstraint=eastwest;strokeColor=inherit;",
                        0, head + a_h, w, 8, parent=box)
            page.vertex(page.uid(f"{cid}_ops"),
                        "<br>".join(_member_html(m, True) for m in methods), member_style,
                        0, head + a_h + 8, w, m_h, parent=box)
    for i, r in enumerate(rels):
        kind = r.get("kind", "association")
        st = "endArrow=open;endSize=12;html=1;rounded=0;edgeStyle=orthogonalEdgeStyle;labelBackgroundColor=default;"
        st = merge_style(st, REL_STYLE.get(kind, REL_STYLE["association"]))
        eid = page.edge(page.uid(f"rel{i}"), esc(r.get("label", "")), merge_style(st, r.get("style")),
                        cell[str(r["from"])], cell[str(r["to"])])
        if r.get("from_mult"):
            page.edge_label(eid, esc(r["from_mult"]), -1, (4, 2))
        if r.get("to_mult"):
            page.edge_label(eid, esc(r["to_mult"]), 1, (-24, 2), style=(
                "edgeLabel;resizable=0;html=1;align=right;verticalAlign=top;labelBackgroundColor=default;"))


# =============================================================== USE CASE
def render_usecase(d, page):
    actors = d.get("actors", [])
    ucs = d.get("use_cases", [])
    a_known = _ids(actors, "actors")
    u_known = _ids(ucs, "use_cases")
    both = {**a_known, **u_known}
    rels = [r for r in d.get("relationships", [])
            if _check_ref(r.get("from"), both, "relationship", page) and _check_ref(r.get("to"), both, "relationship", page)]
    # column of each use case: 0 for directly used, +1 per include/extend hop
    col = {u: 0 for u in u_known}
    for _ in range(len(u_known)):
        changed = False
        for r in rels:
            k = r.get("kind", "association")
            f, t = str(r["from"]), str(r["to"])
            if k == "include" and f in u_known and t in u_known and col[t] < col[f] + 1:
                col[t] = col[f] + 1
                changed = True
            if k == "extend" and f in u_known and t in u_known and col[f] < col[t] + 1:
                col[f] = col[t] + 1
                changed = True
        if not changed:
            break
    ncols = max(col.values(), default=0) + 1
    left = [a for a in a_known if a_known[a].get("side", "left") != "right"]
    right = [a for a in a_known if a_known[a].get("side") == "right"]
    order_a = {a: i for i, a in enumerate(left + right)}
    neighbours = {u: [] for u in u_known}
    for r in rels:
        f, t = str(r["from"]), str(r["to"])
        if f in a_known and t in u_known:
            neighbours[t].append(order_a[f])
        if t in a_known and f in u_known:
            neighbours[f].append(order_a[t])
    columns = [[] for _ in range(ncols)]
    for u in u_known:
        columns[col[u]].append(u)
    rank_in = {}
    for c in range(ncols):
        def key(u):
            nb = neighbours[u]
            if not nb:
                linked = [rank_in[str(r["from"])] for r in rels if str(r["to"]) == u and str(r["from"]) in rank_in]
                linked += [rank_in[str(r["to"])] for r in rels if str(r["from"]) == u and str(r["to"]) in rank_in]
                return sum(linked) / len(linked) if linked else 1e9
            return sum(nb) / len(nb)
        columns[c].sort(key=key)
        for i, u in enumerate(columns[c]):
            rank_in[u] = i
    uc_size = {u: fit_box(u_known[u].get("label", u), 150, 60, 200, pad_w=50, pad_h=30) for u in u_known}
    col_w = [max((uc_size[u][0] for u in columns[c]), default=150) for c in range(ncols)]
    gap_y, gap_x, pad = 25, 70, 40
    col_h = [sum(uc_size[u][1] for u in columns[c]) + gap_y * max(0, len(columns[c]) - 1) for c in range(ncols)]
    inner_h = max(col_h + [200])
    sys_w = sum(col_w) + gap_x * (ncols - 1) + 2 * pad
    sys_w = max(sys_w, text_width(d.get("system", "System"), bold=True) + 40)
    sys_h = inner_h + 2 * pad + 20
    actor_col_w = 140
    sys_x = MARGIN + (actor_col_w if left else 0)
    sys_y = MARGIN
    boundary = page.vertex(page.uid("system"), f"<b>{esc(d.get('system', 'System'))}</b>",
                           "rounded=0;whiteSpace=wrap;html=1;verticalAlign=top;container=1;collapsible=0;"
                           "fillColor=#fafafa;strokeColor=#666666;fontSize=14;",
                           sys_x, sys_y, sys_w, sys_h)
    cell = {}
    uc_center = {}
    x = pad
    for c in range(ncols):
        y = pad + 20 + (inner_h - col_h[c]) / 2
        for u in columns[c]:
            w, h = uc_size[u]
            ux = x + (col_w[c] - w) / 2
            cell[u] = page.vertex(page.uid(u), esc(u_known[u].get("label", u)),
                                  merge_style("ellipse;whiteSpace=wrap;html=1;" + colors("blue"),
                                              u_known[u].get("style")),
                                  ux, y, w, h, parent=boundary,
                                  meta={"spec_id": u, "title": u_known[u].get("label", u),
                                        "description": u_known[u].get("description"),
                                        "kind": "use case"})
            uc_center[u] = sys_y + y + h / 2
            y += h + gap_y
        x += col_w[c] + gap_x

    def place_actors(names, ax):
        slots = []
        for a in names:
            ys = [uc_center[str(r["to"])] for r in rels if str(r["from"]) == a and str(r["to"]) in uc_center]
            ys += [uc_center[str(r["from"])] for r in rels if str(r["to"]) == a and str(r["from"]) in uc_center]
            slots.append((sum(ys) / len(ys) if ys else sys_y + sys_h / 2, a))
        slots.sort()
        last = -1e9
        for want, a in slots:
            y = max(want - 30, last + 110)
            last = y
            label = a_known[a].get("label", a)
            ameta = {"spec_id": a, "title": label, "description": a_known[a].get("description"),
                     "kind": "actor"}
            st = ("shape=umlActor;verticalLabelPosition=bottom;verticalAlign=top;html=1;outlineConnect=0;"
                  "whiteSpace=wrap;")
            if a_known[a].get("kind") == "system":
                st = ("rounded=0;whiteSpace=wrap;html=1;" + colors("gray"))
                ameta["kind"] = "system actor"
                cell[a] = page.vertex(page.uid(a), f"«system»<br>{esc(label)}", st, ax - 25, y, 110, 60,
                                      meta=ameta)
            else:
                cell[a] = page.vertex(page.uid(a), esc(label), merge_style(st, a_known[a].get("style")),
                                      ax, y, 30, 60, meta=ameta)
    place_actors(left, MARGIN + 20)
    place_actors(right, sys_x + sys_w + 80)
    for i, r in enumerate(rels):
        k = r.get("kind", "association")
        f, t = str(r["from"]), str(r["to"])
        if k == "include":
            st, lbl = "endArrow=open;endFill=0;dashed=1;html=1;endSize=10;", "«include»"
        elif k == "extend":
            st, lbl = "endArrow=open;endFill=0;dashed=1;html=1;endSize=10;", "«extend»"
            if r.get("condition"):
                lbl += f"<br>[{esc(r['condition'])}]"
        elif k == "generalization":
            st, lbl = "endArrow=block;endFill=0;html=1;endSize=14;", ""
        else:
            st, lbl = "endArrow=none;html=1;", esc(r.get("label", ""))
        st += "labelBackgroundColor=default;"
        page.edge(page.uid(f"uc_rel{i}"), lbl, merge_style(st, r.get("style")), cell[f], cell[t])


# ======================================================= NESTED (arch/component/deployment)
ARCH_KINDS = {
    "user": ("shape=umlActor;verticalLabelPosition=bottom;verticalAlign=top;html=1;outlineConnect=0;", "icon"),
    "actor": ("shape=umlActor;verticalLabelPosition=bottom;verticalAlign=top;html=1;outlineConnect=0;", "icon"),
    "client": ("rounded=1;whiteSpace=wrap;html=1;" + colors("gray"), "box"),
    "web": ("rounded=1;whiteSpace=wrap;html=1;" + colors("gray"), "box"),
    "mobile": ("rounded=1;whiteSpace=wrap;html=1;arcSize=20;" + colors("gray"), "box"),
    "cdn": ("ellipse;shape=cloud;whiteSpace=wrap;html=1;" + colors("purple"), "cloud"),
    "dns": ("ellipse;shape=cloud;whiteSpace=wrap;html=1;" + colors("purple"), "cloud"),
    "internet": ("ellipse;shape=cloud;whiteSpace=wrap;html=1;" + colors("white"), "cloud"),
    "load_balancer": ("shape=hexagon;perimeter=hexagonPerimeter2;whiteSpace=wrap;html=1;fixedSize=1;size=15;"
                      + colors("purple"), "box"),
    "api_gateway": ("shape=hexagon;perimeter=hexagonPerimeter2;whiteSpace=wrap;html=1;fixedSize=1;size=15;"
                    + colors("orange"), "box"),
    "firewall": ("shape=hexagon;perimeter=hexagonPerimeter2;whiteSpace=wrap;html=1;fixedSize=1;size=15;"
                 + colors("red"), "box"),
    "service": ("rounded=1;whiteSpace=wrap;html=1;" + colors("blue"), "box"),
    "function": ("rounded=1;whiteSpace=wrap;html=1;dashed=1;" + colors("orange"), "box"),
    "worker": ("shape=process;whiteSpace=wrap;html=1;backgroundOutline=1;size=0.1;" + colors("blue"), "box"),
    "auth": ("rounded=1;whiteSpace=wrap;html=1;" + colors("red"), "box"),
    "database": ("shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=12;"
                 + colors("green"), "db"),
    "cache": ("shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=12;"
              + colors("red"), "db"),
    "search": ("shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=12;"
               + colors("yellow"), "db"),
    "storage": ("shape=folder;whiteSpace=wrap;html=1;tabWidth=40;tabHeight=12;tabPosition=left;"
                + colors("green"), "box"),
    "queue": ("shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=12;"
              "direction=south;" + colors("yellow"), "queue"),
    "topic": ("shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=12;"
              "direction=south;" + colors("orange"), "queue"),
    "external": ("rounded=0;whiteSpace=wrap;html=1;dashed=1;" + colors("gray"), "box"),
    "monitoring": ("rounded=1;whiteSpace=wrap;html=1;" + colors("yellow"), "box"),
    "container": ("rounded=0;whiteSpace=wrap;html=1;" + colors("blue"), "box"),
    "generic": ("rounded=0;whiteSpace=wrap;html=1;" + colors("white"), "box"),
}

GROUP_STYLES = {
    "cloud": ("rounded=1;arcSize=2;whiteSpace=wrap;html=1;verticalAlign=top;align=left;spacingLeft=10;"
              "fontStyle=1;dashed=0;fillColor=#f7f9fc;strokeColor=#6c8ebf;"),
    "region": ("rounded=1;arcSize=2;whiteSpace=wrap;html=1;verticalAlign=top;align=left;spacingLeft=10;"
               "fontStyle=1;dashed=1;fillColor=#f0f7ff;strokeColor=#6c8ebf;"),
    "vpc": ("rounded=0;whiteSpace=wrap;html=1;verticalAlign=top;align=left;spacingLeft=10;fontStyle=1;"
            "dashed=0;fillColor=#f2f9f0;strokeColor=#82b366;"),
    "subnet": ("rounded=0;whiteSpace=wrap;html=1;verticalAlign=top;align=left;spacingLeft=10;fontStyle=1;"
               "dashed=1;fillColor=#fbfdf9;strokeColor=#82b366;"),
    "cluster": ("rounded=1;arcSize=3;whiteSpace=wrap;html=1;verticalAlign=top;align=left;spacingLeft=10;"
                "fontStyle=1;dashed=1;fillColor=#f5f0fa;strokeColor=#9673a6;"),
    "zone": ("rounded=0;whiteSpace=wrap;html=1;verticalAlign=top;align=left;spacingLeft=10;fontStyle=1;"
             "dashed=1;fillColor=none;strokeColor=#999999;"),
    "boundary": ("rounded=0;whiteSpace=wrap;html=1;verticalAlign=top;align=left;spacingLeft=10;fontStyle=1;"
                 "dashed=1;dashPattern=8 4;fillColor=none;strokeColor=#b85450;"),
    "group": ("rounded=1;arcSize=3;whiteSpace=wrap;html=1;verticalAlign=top;align=left;spacingLeft=10;"
              "fontStyle=1;dashed=0;fillColor=#fafafa;strokeColor=#999999;"),
    "package": ("shape=folder;fontStyle=1;tabWidth=110;tabHeight=22;tabPosition=left;html=1;whiteSpace=wrap;"
                "verticalAlign=top;align=left;spacingLeft=8;spacingTop=-2;fillColor=#fafafa;strokeColor=#666666;"),
    "subsystem": ("rounded=0;whiteSpace=wrap;html=1;verticalAlign=top;fontStyle=0;"
                  "fillColor=#f7f9fc;strokeColor=#6c8ebf;"),
    "node": ("shape=cube;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;darkOpacity=0.05;"
             "darkOpacity2=0.1;size=12;verticalAlign=top;align=left;spacingLeft=8;fillColor=#f5f5f5;"
             "strokeColor=#666666;"),
    "device": ("shape=cube;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;darkOpacity=0.05;"
               "darkOpacity2=0.1;size=12;verticalAlign=top;align=left;spacingLeft=8;fillColor=#e8eef7;"
               "strokeColor=#6c8ebf;"),
    "execution_environment": ("shape=cube;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;"
                              "darkOpacity=0.05;darkOpacity2=0.1;size=10;verticalAlign=top;align=left;"
                              "spacingLeft=8;fillColor=#ffffff;strokeColor=#82b366;"),
    "component": ("html=1;dropTarget=0;whiteSpace=wrap;verticalAlign=top;" + colors("blue")),
}

LEAF_SIZES = {"icon": (100, 90), "cloud": (140, 80), "db": (120, 80), "queue": (140, 60), "box": (140, 60)}


def _nested_common(d, page, mode):
    elements = d.get("elements") or d.get("nodes") or d.get("components") or []
    flat, children, roots = {}, {}, []

    def walk(items, parent):
        for el in items:
            eid = str(_need(el, "id", "element"))
            if eid in flat:
                raise SpecError(f"duplicate element id '{eid}'")
            flat[eid] = el
            (children.setdefault(parent, []) if parent else roots).append(eid)
            if el.get("children"):
                walk(el["children"], eid)
    walk(elements, None)

    # interface lollipops for component diagrams
    iface_nodes = {}
    extra_edges = []
    if mode == "component":
        # lollipops are clickable too: their job comes from the diagram-level
        # {"interfaces": {"Name": "description"}} map
        iface_desc = d.get("interfaces") or {}
        for eid, el in list(flat.items()):
            for iname in el.get("provides", []):
                iid = f"if_{slug(iname)}"
                if iid not in iface_nodes:
                    iface_nodes[iid] = iname
                    parent = next((p for p, ks in children.items() if eid in ks), None)
                    (children.setdefault(parent, []) if parent else roots).append(iid)
                    flat[iid] = {"id": iid, "label": iname, "kind": "interface",
                                 "description": iface_desc.get(iname)}
                extra_edges.append((eid, iid, "provides"))
            for iname in el.get("requires", []):
                iid = f"if_{slug(iname)}"
                if iid not in iface_nodes:
                    iface_nodes[iid] = iname
                    roots.append(iid)
                    flat[iid] = {"id": iid, "label": iname, "kind": "interface",
                                 "description": iface_desc.get(iname)}
                extra_edges.append((eid, iid, "requires"))

    sizes, header, minw, style, label = {}, {}, {}, {}, {}
    inset = {}  # eid -> (dx, dy, w, h) when the drawn shape is smaller than its layout slot
    info = {}   # eid -> descriptive metadata for the interactive HTML panel
    for eid, el in flat.items():
        kind = el.get("kind")
        name = el.get("label", eid)
        stereo = el.get("stereotype")
        tech = el.get("technology") or el.get("tech")
        info[eid] = {"spec_id": el.get("id", eid), "title": name,
                     "description": el.get("description"), "technology": tech,
                     "stereotype": stereo, "kind": kind}
        is_container = bool(children.get(eid))
        if mode == "architecture":
            default_kind = "group" if is_container else "service"
        elif mode == "component":
            default_kind = "component"
        else:
            default_kind = "node"
        kind = kind or default_kind
        info[eid]["kind"] = kind
        sub = f"<br><font style='font-size:10px' color='#555555'>[{esc(tech)}]</font>" if tech else ""
        st_txt = f"«{esc(stereo)}»<br>" if stereo else ""
        if kind == "interface":
            style[eid] = ("ellipse;html=1;verticalLabelPosition=bottom;verticalAlign=top;"
                          "labelBackgroundColor=default;" + colors("white"))
            label[eid] = esc(name)
            sizes[eid] = (max(40, text_width(name) + 10), 46)
            inset[eid] = ((sizes[eid][0] - 20) / 2, 0, 20, 20)
            continue
        if is_container:
            base = GROUP_STYLES.get(kind) or GROUP_STYLES["group"]
            if kind == "component":
                label[eid] = f"«component»<br><b>{esc(name)}</b>{sub}"
                header[eid] = 50
            elif kind in ("node", "device", "execution_environment"):
                s = stereo or {"node": None, "device": "device",
                               "execution_environment": "executionEnvironment"}[kind]
                label[eid] = (f"«{esc(s)}»<br>" if s else "") + f"<b>{esc(name)}</b>{sub}"
                header[eid] = 34 + (14 if s else 0) + (14 if tech else 0) + 8
            elif kind == "subsystem":
                label[eid] = f"«subsystem»<br><b>{esc(name)}</b>"
                header[eid] = 44
            else:
                label[eid] = st_txt + (esc(name) if "fontStyle=1" in base else f"<b>{esc(name)}</b>") + sub
                header[eid] = 30 + (14 if stereo else 0) + (14 if tech else 0)
            style[eid] = merge_style(base + "container=1;collapsible=0;", el.get("style"))
            minw[eid] = text_width(name, bold=True) + (140 if kind == "package" else 60)
            continue
        # leaves
        if mode == "architecture" or kind in ARCH_KINDS:
            base, shape = ARCH_KINDS.get(kind, ARCH_KINDS["service"])
            if kind not in ARCH_KINDS:
                page.warnings.append(f"element {eid}: unknown kind '{kind}', using service")
            lbl = st_txt + (f"<b>{esc(name)}</b>" if shape != "icon" else esc(name)) + sub
            mw, mh = LEAF_SIZES[shape]
            if shape == "icon":
                style[eid] = merge_style(base, el.get("style"))
                sizes[eid] = (max(mw, text_width(name) + 20), mh)
                inset[eid] = ((sizes[eid][0] - 30) / 2, 0, 30, 60)
            else:
                w, h = fit_box(name + ("\n[" + tech + "]" if tech else "") + ("\n«" + stereo + "»" if stereo else ""),
                               mw, mh, 220, bold=True, pad_w=40 if shape in ("cloud", "db", "queue") else 28)
                sizes[eid] = (w, h + (10 if shape == "db" else 0))
                style[eid] = merge_style(base, el.get("style"))
            label[eid] = lbl
        elif kind == "component":
            label[eid] = (f"«{esc(stereo or 'component')}»<br><b>{esc(name)}</b>{sub}")
            w, h = fit_box(f"«{stereo or 'component'}»\n{name}" + (f"\n[{tech}]" if tech else ""),
                           160, 70, 240, pad_w=60, bold=True)
            sizes[eid] = (w, h)
            style[eid] = merge_style(GROUP_STYLES["component"], el.get("style"))
        elif kind == "artifact":
            label[eid] = f"«artifact»<br><b>{esc(name)}</b>{sub}"
            sizes[eid] = fit_box(f"«artifact»\n{name}" + (f"\n{tech}" if tech else ""), 140, 60, 220, bold=True)
            style[eid] = merge_style("shape=note;size=14;whiteSpace=wrap;html=1;" + colors("yellow"), el.get("style"))
        elif kind in ("node", "device", "execution_environment", "database_server"):
            s = stereo or {"node": None, "device": "device", "execution_environment": "executionEnvironment",
                           "database_server": "database"}[kind]
            label[eid] = (f"«{esc(s)}»<br>" if s else "") + f"<b>{esc(name)}</b>{sub}"
            sizes[eid] = fit_box((f"«{s}»\n" if s else "") + name + (f"\n[{tech}]" if tech else ""),
                                 160, 80, 240, bold=True, pad_w=40, pad_h=30)
            style[eid] = merge_style(GROUP_STYLES.get(kind, GROUP_STYLES["node"]).replace(
                "verticalAlign=top;align=left;", "verticalAlign=middle;align=center;"), el.get("style"))
        else:
            label[eid] = st_txt + f"<b>{esc(name)}</b>{sub}"
            sizes[eid] = fit_box(name, 140, 60, 220, bold=True)
            style[eid] = merge_style("rounded=0;whiteSpace=wrap;html=1;" + colors("white"), el.get("style"))

    conns = [c for c in (d.get("connections") or d.get("dependencies") or [])
             if _check_ref(c.get("from"), flat, "connection", page) and _check_ref(c.get("to"), flat, "connection", page)]
    pairs = [(str(c["from"]), str(c["to"])) for c in conns] + [(a, b) for a, b, _ in extra_edges]
    default_dir = "TB" if mode == "component" else "LR"
    rel, sizes, _ = nested_layout(roots, children, sizes, pairs, d.get("direction", default_dir),
                                  pad=40, header=header, min_width=minw, node_gap=50,
                                  rank_gap=_label_gap([c.get("label") for c in conns],
                                                      d.get("direction", default_dir),
                                                      110 if mode != "component" else 90))
    cell = {}

    def place(items, parent_cell):
        for eid in items:
            x, y = rel[eid]
            if parent_cell == "1":
                x, y = x + MARGIN, y + MARGIN
            w, h = sizes[eid]
            st = style[eid]
            if eid in inset:
                dx, dy, iw, ih = inset[eid]
                cell[eid] = page.vertex(page.uid(eid), label[eid], st, x + dx, y + dy, iw, ih,
                                        parent=parent_cell, meta=info.get(eid))
            else:
                cell[eid] = page.vertex(page.uid(eid), label[eid], st, x, y, w, h,
                                        parent=parent_cell, meta=info.get(eid))
            if (flat[eid].get("kind") or ("component" if mode == "component" else "")) == "component":
                page.vertex(page.uid(f"{eid}_icon"), "", "shape=module;jettyWidth=8;jettyHeight=4;html=1;",
                            w - 30, 8, 20, 22, parent=cell[eid])
            if children.get(eid):
                place(children[eid], cell[eid])
    place(roots, "1")

    routes = page.route_edges([(cell[str(c["from"])], cell[str(c["to"])]) for c in conns] +
                              [(cell[a], cell[b]) for a, b, _ in extra_edges])
    for i, c in enumerate(conns):
        kind = c.get("kind", "sync")
        lbl = c.get("label", "")
        if c.get("protocol"):
            lbl = f"{lbl} ({c['protocol']})" if lbl else c["protocol"]
        if mode == "deployment":
            st = EDGE + "endArrow=none;"
            if c.get("protocol") and not c.get("label"):
                lbl = f"«{c['protocol']}»"
        elif mode == "component":
            st = EDGE + "endArrow=open;endFill=0;dashed=1;endSize=10;"
            if kind == "sync":
                kind = "use"
            if not lbl and kind in ("use", "uses"):
                lbl = "«use»"
        else:
            st = EDGE + "endArrow=block;endFill=1;"
        if kind in ("async", "event", "publish", "subscribe"):
            st += "dashed=1;endFill=0;endArrow=open;"
        if c.get("bidirectional"):
            st += "startArrow=block;startFill=1;"
        src, tgt = cell[str(c["from"])], cell[str(c["to"])]
        points, anchor = routes[i]
        page.edge(page.uid(f"c{i}"), esc(lbl), merge_style(merge_style(st, anchor), c.get("style")),
                  src, tgt, points=points)
    for i, (a, b, how) in enumerate(extra_edges):
        pts, anchor = routes[len(conns) + i]
        if how == "provides":
            page.edge(page.uid(f"prov{i}"), "", merge_style("endArrow=none;html=1;rounded=0;", anchor),
                      cell[a], cell[b], points=pts)
        else:
            page.edge(page.uid(f"req{i}"), "«use»",
                      merge_style(EDGE + "endArrow=open;endFill=0;dashed=1;endSize=10;", anchor),
                      cell[a], cell[b], points=pts)


def render_architecture(d, page):
    _nested_common(d, page, "architecture")


def render_component(d, page):
    _nested_common(d, page, "component")


def render_deployment(d, page):
    _nested_common(d, page, "deployment")


# =============================================================== SEQUENCE
LIFELINE = ("shape=umlLifeline;perimeter=lifelinePerimeter;whiteSpace=wrap;html=1;container=1;dropTarget=0;"
            "collapsible=0;recursiveResize=0;outlineConnect=0;portConstraint=eastwest;"
            "newEdgeStyle={\"curved\":0,\"rounded\":0};")
PARTICIPANT_STYLE = {
    "actor": "participant=umlActor;verticalAlign=top;spacingTop=36;labelBackgroundColor=default;whiteSpace=nowrap;",
    "boundary": "participant=umlBoundary;verticalAlign=top;spacingTop=36;labelBackgroundColor=default;whiteSpace=nowrap;",
    "control": "participant=umlControl;verticalAlign=top;spacingTop=36;labelBackgroundColor=default;whiteSpace=nowrap;",
    "entity": "participant=umlEntity;verticalAlign=top;spacingTop=36;labelBackgroundColor=default;whiteSpace=nowrap;",
}
BAR_W = 10
BAR_STEP = 6


def render_sequence(d, page):
    parts = d.get("participants", [])
    known = _ids(parts, "participants")
    order = list(known)
    items = d.get("messages", [])
    # validate
    for i, m in enumerate(items):
        if m.get("type") == "note":
            for o in m.get("over", []):
                _check_ref(o, known, f"note #{i}", page)
            continue
        if str(m.get("from")) not in known or str(m.get("to")) not in known:
            raise SpecError(f"message #{i} ({m.get('label', '')}): unknown participant "
                            f"'{m.get('from')}' or '{m.get('to')}'. Known: {order}")
    idx = {p: i for i, p in enumerate(order)}
    head_w = {}
    for p in order:
        k = known[p].get("kind", "participant")
        lbl = known[p].get("label", p)
        if k in PARTICIPANT_STYLE:
            head_w[p] = max(60, text_width(lbl) + 16)
        else:
            head_w[p] = max(110, text_width(lbl, bold=True) + 30 + (0 if k != "database" else 0))
    # spacing between neighbouring lifelines
    gaps = [max(60, (head_w[order[i]] + head_w[order[i + 1]]) / 2 + 40) for i in range(len(order) - 1)]
    for m in items:
        if m.get("type") == "note":
            continue
        a, b = sorted((idx[str(m["from"])], idx[str(m["to"])]))
        need = text_width(m.get("label", "")) + 50
        if a == b:
            if a < len(gaps):
                gaps[a] = max(gaps[a], need + 30)
            continue
        have = sum(gaps[a:b])
        if have < need:
            extra = (need - have) / (b - a)
            for k in range(a, b):
                gaps[k] += extra
    cx = {}
    x = MARGIN + head_w[order[0]] / 2
    for i, p in enumerate(order):
        cx[p] = x
        if i < len(gaps):
            x += gaps[i]
    top = MARGIN + 30 if d.get("fragments") else MARGIN
    head_h = {p: (60 if known[p].get("kind") in PARTICIPANT_STYLE else 40) for p in order}
    max_head = max(head_h.values())

    # fragments bookkeeping
    frags = d.get("fragments", [])
    starting, ending, elses = {}, {}, {}
    for f in frags:
        s, e = int(f.get("from", f.get("start", 0))), int(f.get("to", f.get("end", 0)))
        if not (0 <= s <= e < len(items)):
            page.warnings.append(f"fragment {f.get('kind')} has invalid range {s}..{e}; skipped")
            continue
        f["_s"], f["_e"] = s, e
        starting.setdefault(s, []).append(f)
        ending.setdefault(e, []).append(f)
        for el in f.get("else", []):
            at = int(el.get("at", -1))
            if s < at <= e:
                elses.setdefault(at, []).append((f, el))

    def level(f):
        inner = [g for g in frags if g is not f and "_s" in g and f["_s"] <= g["_s"] and g["_e"] <= f["_e"]
                 and (g["_s"], -g["_e"]) > (f["_s"], -f["_e"])]
        return 1 + max((level(g) for g in inner), default=0)
    for f in frags:
        if "_s" in f:
            f["_lvl"] = level(f)
    for k in starting:
        starting[k].sort(key=lambda f: -(f["_e"] - f["_s"]))
    for k in ending:
        ending[k].sort(key=lambda f: f["_e"] - f["_s"])

    stacks = {p: [] for p in order}   # open bars: list of [start_y, depth]
    bars = []                         # (participant, y0, y1, depth)
    last_y = {p: None for p in order}
    created_at = {}
    destroyed_at = {}
    for m in items:
        if m.get("type") == "create":
            created_at[str(m["to"])] = None

    def edge_x(p, towards_right):
        st = stacks[p]
        if not st:
            return cx[p]
        depth = st[-1][1]
        left = cx[p] - BAR_W / 2 + (depth - 1) * BAR_STEP
        return left + BAR_W if towards_right else left

    y = top + max_head + 30
    rows = []
    for i, m in enumerate(items):
        for f in starting.get(i, []):
            f["_top"] = y
            y += 34
        for f, el in elses.get(i, []):
            f.setdefault("_seps", []).append((y, el.get("condition", el.get("label", "else"))))
            y += 30
        mtype = m.get("type", "sync")
        if mtype == "note":
            over = [o for o in m.get("over", []) if o in known] or order[:1]
            text = m.get("text", m.get("label", ""))
            xs = [cx[o] for o in over]
            nw = max(max(xs) - min(xs) + 80, text_width(text) + 30, 100)
            nh = fit_box(text, nw, 30, nw)[1]
            rows.append(("note", i, y, (min(xs) + max(xs)) / 2 - nw / 2, nw, nh, text))
            y += nh + 20
        else:
            f, t = str(m["from"]), str(m["to"])
            label = m.get("label", "")
            nlines = max(1, str(label).count("\n") + 1)
            y += 16 * nlines + 4
            my = y
            for p in (f, t):
                last_y[p] = my
            if f == t:  # self message
                if not stacks[f] and known[f].get("kind") not in ("actor",):
                    stacks[f].append([my - 10, 1])
                sx = edge_x(f, True)
                depth = (stacks[f][-1][1] if stacks[f] else 0) + 1
                bars.append((f, my + 14, my + 40, depth))
                tx = cx[f] - BAR_W / 2 + (depth - 1) * BAR_STEP + BAR_W
                rows.append(("self", i, my, sx, tx, mtype, label))
                y = my + 50
                last_y[f] = my + 40
            else:
                right = cx[t] > cx[f]
                if mtype in ("sync", "async", "create") and not stacks[f] \
                        and known[f].get("kind") not in ("actor",) and f not in destroyed_at:
                    stacks[f].append([my - 10, 1])
                sx = edge_x(f, right)
                if mtype == "create":
                    created_at[t] = my - head_h[t] / 2
                    tx = cx[t] - head_w[t] / 2 if right else cx[t] + head_w[t] / 2
                    stacks[t].append([my + head_h[t] / 2 + 4, 1])
                elif mtype == "sync":
                    depth = (stacks[t][-1][1] + 1) if stacks[t] else 1
                    stacks[t].append([my, depth])
                    tx = edge_x(t, not right)
                elif mtype == "reply":
                    if stacks[f]:
                        s0, dep = stacks[f].pop()
                        bars.append((f, s0, my, dep))
                    tx = edge_x(t, not right)
                elif mtype == "destroy":
                    tx = cx[t]
                    while stacks[t]:
                        s0, dep = stacks[t].pop()
                        bars.append((t, s0, my, dep))
                    destroyed_at[t] = my
                else:  # async
                    tx = edge_x(t, not right)
                rows.append(("msg", i, my, sx, tx, mtype, label))
                y = my + 18
        for f in ending.get(i, []):
            y += 8
            f["_bottom"] = y
            y += 12
    end_y = y + 30
    for p in order:
        while stacks[p]:
            s0, dep = stacks[p].pop()
            stop = max((last_y[p] or s0) + 12, s0 + 20)
            bars.append((p, s0, stop, dep))

    # lifelines
    life = {}
    for p in order:
        k = known[p].get("kind", "participant")
        lbl = known[p].get("label", p)
        w = head_w[p] if k not in PARTICIPANT_STYLE else 40
        ty = created_at.get(p) if created_at.get(p) is not None else top + (max_head - head_h[p])
        bottom = destroyed_at.get(p, end_y)
        style = LIFELINE + f"size={head_h[p] if k not in PARTICIPANT_STYLE else 60};"
        if k in PARTICIPANT_STYLE:
            style += PARTICIPANT_STYLE[k]
            value = esc(lbl)
        else:
            fill = {"database": "green", "queue": "yellow", "external": "gray",
                    "collections": "purple"}.get(k, "blue")
            style += colors(fill)
            value = (f"«{esc(k)}»<br>" if k in ("database", "queue", "external", "collections") else "") \
                + f"<b>{esc(lbl)}</b>"
            if k in ("database", "queue", "external", "collections"):
                style += f"size={head_h[p] + 6};"
        style = merge_style(style, known[p].get("style"))
        life[p] = page.vertex(page.uid(f"ll_{p}"), value, style, cx[p] - w / 2, ty, w, bottom - ty,
                              meta={"spec_id": p, "title": lbl,
                                    "description": known[p].get("description"), "kind": k})
        if p in destroyed_at:
            page.vertex(page.uid(f"x_{p}"), "", "shape=umlDestroy;html=1;strokeWidth=3;strokeColor=#b85450;",
                        cx[p] - 10, destroyed_at[p] - 10, 20, 20)
    # activation bars (children of lifelines so they move together)
    for p, y0, y1, depth in sorted(bars, key=lambda b: (b[3], b[1])):
        lx, ly = page.abs[life[p]][:2]
        bx = cx[p] - BAR_W / 2 + (depth - 1) * BAR_STEP
        page.vertex(page.uid(f"act_{p}"), "",
                    "html=1;points=[];perimeter=orthogonalPerimeter;outlineConnect=0;targetShapes=umlLifeline;"
                    "portConstraint=eastwest;newEdgeStyle={\"curved\":0,\"rounded\":0};fillColor=#ffffff;",
                    bx - lx, y0 - ly, BAR_W, max(10, y1 - y0), parent=life[p])
    # fragments
    for f in frags:
        if "_top" not in f:
            continue
        involved = set()
        self_right = False
        for j in range(f["_s"], f["_e"] + 1):
            m = items[j]
            if m.get("type") == "note":
                involved.update(o for o in m.get("over", []) if o in known)
            else:
                involved.update((str(m["from"]), str(m["to"])))
                if str(m["from"]) == str(m["to"]):
                    self_right = True
        xs = [cx[p] for p in involved] or [cx[order[0]]]
        margin = 30 + 14 * (f["_lvl"] - 1)
        x0 = min(xs) - margin - (head_w[min(involved, key=lambda p: cx[p])] / 4 if involved else 0)
        x1 = max(xs) + margin + (50 if self_right else 0)
        kind = f.get("kind", "opt")
        cond = f.get("condition", f.get("label", ""))
        tab_w = max(50, text_width(kind, bold=True) + 24)
        x1 = max(x1, x0 + tab_w + text_width(cond) + 40)
        frame = page.vertex(page.uid(f"frag_{kind}"), f"<b>{esc(kind)}</b>",
                            f"shape=umlFrame;whiteSpace=wrap;html=1;pointerEvents=0;recursiveResize=0;"
                            f"container=0;collapsible=0;width={tab_w};height=22;fillColor=none;"
                            f"strokeColor=#666666;swimlaneFillColor=none;",
                            x0, f["_top"] + 2, x1 - x0, f["_bottom"] - f["_top"] - 2)
        if cond:
            page.vertex(page.uid("guard"), f"[{esc(cond)}]",
                        "text;html=1;align=left;verticalAlign=middle;fontStyle=2;",
                        x0 + tab_w + 8, f["_top"] + 2, text_width(cond) + 30, 22)
        for sy, sc in f.get("_seps", []):
            page.edge(page.uid("sep"), "", "endArrow=none;dashed=1;html=1;strokeColor=#666666;",
                      source_point=(x0, sy), target_point=(x1, sy))
            page.vertex(page.uid("guard"), f"[{esc(sc)}]",
                        "text;html=1;align=left;verticalAlign=middle;fontStyle=2;",
                        x0 + 8, sy + 2, text_width(sc) + 30, 20)
        _ = frame
    # messages
    msg_no = {}
    for j, m in enumerate(items):
        if m.get("type") != "note":
            msg_no[j] = len(msg_no) + 1
    for row in rows:
        if row[0] == "note":
            _, i, ny, nx, nw, nh, text = row
            page.vertex(page.uid("note"), esc(text),
                        "shape=note;whiteSpace=wrap;html=1;size=10;fillColor=#fff2cc;strokeColor=#d6b656;align=left;"
                        "spacingLeft=6;", nx, ny, nw, nh)
            continue
        kind, i, my, sx, tx, mtype, label = row
        m = items[i]
        num = f"{msg_no[i]}. " if d.get("autonumber") else ""
        text = esc(num + str(label))
        base = "html=1;verticalAlign=bottom;curved=0;rounded=0;labelBackgroundColor=default;"
        if mtype == "reply":
            st = base + "endArrow=open;endFill=0;dashed=1;endSize=8;"
        elif mtype in ("async",):
            st = base + "endArrow=open;endFill=0;endSize=8;"
        elif mtype == "create":
            st = base + "endArrow=open;endFill=0;dashed=1;endSize=8;"
            text = esc(num) + "«create»" + (f" {esc(label)}" if label else "")
        elif mtype == "destroy":
            st = base + "endArrow=open;endFill=0;dashed=1;endSize=8;"
            text = esc(num) + "«destroy»" + (f" {esc(label)}" if label else "")
        else:
            st = base + "endArrow=block;endFill=1;endSize=8;"
        st = merge_style(st, m.get("style"))
        if kind == "self":
            loop_x = max(sx, tx) + 30
            page.edge(page.uid(f"m{i}"), text, merge_style(st, "align=left;spacingLeft=4;"),
                      source_point=(sx, my), target_point=(tx, my + 26),
                      points=[(loop_x, my), (loop_x, my + 26)])
        else:
            page.edge(page.uid(f"m{i}"), text, st, source_point=(sx, my), target_point=(tx, my))


# ================================================================== DRIVER
RENDERERS = {
    "sequence": render_sequence,
    "flowchart": render_flowchart,
    "er": render_er,
    "erd": render_er,
    "class": render_class,
    "architecture": render_architecture,
    "usecase": render_usecase,
    "use_case": render_usecase,
    "activity": render_activity,
    "state": render_state,
    "state_machine": render_state,
    "component": render_component,
    "deployment": render_deployment,
}


def _page_warnings(page):
    """Cheap structural self-check on the in-memory Page. Catches dangling
    edges and zero-size vertices — the generator bugs that would otherwise only
    surface on screen."""
    probs = []
    for c in page.cells:
        if c.is_vertex and not c.relative:
            if float(c.w or 0) <= 0 or float(c.h or 0) <= 0:
                probs.append(f"vertex '{c.id}' has zero size")
        if c.is_edge:
            has_pts = c.source_point is not None or c.target_point is not None
            if (c.source is None or c.target is None) and not has_pts:
                probs.append(f"edge '{c.id}' is dangling (no source/target or points)")
    # every clickable box should explain itself when picked, in terms of its job
    for m in page.meta.values():
        sid = m.get("spec_id") or m.get("title")
        desc = str(m.get("description") or "").strip()
        if not desc:
            probs.append(f"missing description: '{sid}'")
            continue
        for issue in _description_issues(desc, str(m.get("title") or "")):
            probs.append(f"weak description: '{sid}': {issue}")
    return probs


#: a description longer than this stops being a quick answer to "what is this
#: box for?" -- the rest belongs in the diagram's key_points.
_DESC_MAX_CHARS = 320


def _description_issues(desc, title):
    """Heuristic lint for descriptions that explain *how the code is wired*
    instead of *what job the component does*. Returns human-readable issues;
    an empty list means the description passes."""
    issues = []
    code_spans = re.findall(r"`([^`]+)`", desc)
    code_chars = sum(len(c) for c in code_spans)
    prose = re.sub(r"`[^`]+`", "", desc)
    if desc.startswith("`"):
        issues.append("starts with a code name -- start with the job the component does, "
                      "in plain words (code names belong in `technology`)")
    elif code_spans and code_chars > 0.35 * len(desc):
        issues.append("mostly code names -- say the job in plain words; move identifiers "
                      "to `technology` or key_points")
    calls = re.findall(r"\b[A-Za-z_][\w.]*\(\)", prose) + \
        [c for c in code_spans if re.search(r"\w\(", c)]
    if len(calls) >= 3:
        issues.append(f"walks through {len(calls)} function calls -- that is implementation "
                      "detail; describe the outcome instead")
    if len(desc) > _DESC_MAX_CHARS:
        issues.append(f"{len(desc)} characters -- keep it to the job plus one 'why' sentence "
                      f"(<= {_DESC_MAX_CHARS}); move the rest to key_points")
    norm = lambda t: re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()
    if title and norm(desc).rstrip(" .") in (norm(title), norm(title) + "s"):
        issues.append("only repeats the label -- say what it does")
    return issues


#: every key a diagram type may use to hold its drillable elements. A single
#: diagram can populate more than one (e.g. usecase has both actors and
#: use_cases), so _iter_elements scans them all rather than the first present.
_ELEMENT_KEYS = ("elements", "nodes", "components", "classes", "entities",
                 "states", "participants", "use_cases", "actors")


def _iter_elements(d):
    """Yield every element dict in a diagram spec, recursing into `children`,
    across all the key names the different diagram types use."""
    def walk(els):
        for el in els:
            if isinstance(el, dict):
                yield el
                if el.get("children"):
                    yield from walk(el["children"])
    for key in _ELEMENT_KEYS:
        items = d.get(key)
        if isinstance(items, list):
            yield from walk(items)


def _intro_of(d):
    """Page-level explanation fields of a diagram spec (all optional)."""
    keys = ("summary", "how_to_read", "steps", "key_points", "glossary")
    intro = {k: d[k] for k in keys if d.get(k)}
    return intro or None


def _canonical_type(t):
    """Diagram type with aliases folded (erd→er, use_case→usecase, ...), so
    two specs can be compared for "same kind of diagram"."""
    t = str(t or "").lower()
    return {"erd": "er", "use_case": "usecase", "state_machine": "state"}.get(t, t)


#: canonical type -> the suffix shown at the end of a diagram's title
_TYPE_TITLES = {
    "sequence": "Sequence diagram", "flowchart": "Flowchart diagram",
    "er": "ER diagram", "class": "Class diagram",
    "architecture": "Architecture diagram", "usecase": "Use case diagram",
    "activity": "Activity diagram", "state": "State diagram",
    "component": "Component diagram", "deployment": "Deployment diagram",
}


def _typed_title(name, dtype):
    """`name` ending with its diagram type ("… — Architecture diagram"), so
    every page title and index entry says what kind of diagram it is."""
    suffix = _TYPE_TITLES.get(_canonical_type(dtype))
    if not suffix or name.lower().endswith(suffix.lower()):
        return name
    return f"{name} — {suffix}"


def _label_gap(labels, direction, base, cap=320):
    """Gap between ranks big enough that an edge label fits between the
    two boxes it joins instead of sitting on top of one of them. Left-right
    layouts need the label's width; top-down ones its height."""
    texts = [str(t) for t in labels if t]
    if not texts:
        return base
    if direction in ("LR", "RL"):
        need = max(text_width(t, 11) for t in texts) + 40
    else:
        need = 16 * max(t.count("<br>") + t.count("\n") + 1 for t in texts) + 44
    return max(base, min(cap, need))


#: diagram types whose renderers create edges without a route; they are routed
#: in one pass once every box is placed. (Nested types route as they render;
#: sequence messages are horizontal by construction.)
_ROUTED_LATE = {"flowchart", "activity", "state", "class", "usecase"}


def _render_detail(detail, parent_label):
    """Render a `detail` sub-spec (a full diagram spec for one component's
    internals) into a laid-out Page. Returns (title, Page) or None on error."""
    if not isinstance(detail, dict):
        return None
    dtype = str(detail.get("type", "")).lower()
    if dtype not in RENDERERS:
        return None
    title = _typed_title(detail.get("name") or f"{parent_label} — detail", dtype)
    page = Page(title)
    try:
        RENDERERS[dtype](detail, page)
    except SpecError:
        return None
    if dtype in _ROUTED_LATE:
        page.route_unrouted()
    page.spread_parallel_edges()
    page.intro = _intro_of(detail)
    return title, page


def _collect_details(d, prefix="", errors=None):
    """Map each element's layer key to its rendered detail Page, for every element
    in diagram `d` that carries a `detail` sub-spec — recursively, so a detail
    diagram's own elements may carry a further `detail`. Top-level keys are the
    element's spec id; nested keys are slash-joined paths ("parent/child").
    Insertion order is pre-order (a parent layer precedes its children), which
    the HTML emitter relies on for stacking.

    A drill-down must be the same diagram type as the diagram it sits in (an
    architecture box zooms into an architecture diagram, a flowchart node into
    a flowchart, ...). A mismatched or unrenderable `detail` is skipped and
    reported in `errors` as a hard error."""
    details = {}
    parent_type = _canonical_type(d.get("type"))
    for el in _iter_elements(d):
        if el.get("detail"):
            sid = str(el.get("id") or el.get("name") or "")
            if not sid:
                continue
            key = f"{prefix}{sid}"
            det = el["detail"]
            dtype = _canonical_type(det.get("type") if isinstance(det, dict) else None)
            if dtype != parent_type:
                if errors is not None:
                    errors.append(f"detail '{key}' is a '{dtype or '?'}' diagram inside a "
                                  f"'{parent_type}' diagram — a drill-down must be the "
                                  f"same type as its parent")
                continue
            rendered = _render_detail(det, el.get("label", sid))
            if rendered:
                details[key] = rendered
                details.update(_collect_details(det, prefix=key + "/", errors=errors))
            elif errors is not None:
                errors.append(f"detail '{key}' could not be rendered (check its spec)")
    return details


def _diagram_fname(n, name, dtype):
    """File name for diagram #n. The type is part of the name when it ends with
    "<Type> diagram"; otherwise append it so every file says what kind it is."""
    s = slug(name)
    if s.endswith("-diagram") or s.endswith(f"-{dtype}"):
        return f"{n:02d}-{s}.html"
    return f"{n:02d}-{s}-{dtype}-diagram.html"


def build(spec, out_dir, combined=None, only=None):
    diagrams = spec.get("diagrams") if isinstance(spec, dict) and "diagrams" in spec else [spec]
    os.makedirs(out_dir, exist_ok=True)
    pages, written, problems, entries = [], [], [], []
    index_path = combined or os.path.join(out_dir, "index.html")
    index_href = os.path.relpath(index_path, out_dir)
    for n, d in enumerate(diagrams, 1):
        dtype = str(d.get("type", "")).lower()
        if only and dtype not in only:
            # not re-rendered this run, but still listed so the index always
            # covers the whole spec (its file is left as previously written)
            sname = _typed_title(d.get("name") or dtype.title(), dtype)
            entries.append((sname, _diagram_fname(n, sname, dtype), d.get("index_blurb")))
            continue
        if dtype not in RENDERERS:
            problems.append(f"diagram #{n}: unknown type '{dtype}' (valid: {', '.join(sorted(RENDERERS))})")
            continue
        name = _typed_title(d.get("name") or dtype.title(), dtype)
        page = Page(name)
        try:
            RENDERERS[dtype](d, page)
        except SpecError as e:
            problems.append(f"{dtype} '{name}': {e}")
            continue
        if dtype in _ROUTED_LATE:
            page.route_unrouted()
        page.spread_parallel_edges()
        page.intro = _intro_of(d)
        fname = _diagram_fname(n, name, dtype)
        path = os.path.join(out_dir, fname)
        detail_errors = []
        details = _collect_details(d, errors=detail_errors)
        problems.extend(f"{dtype} '{name}': {e}" for e in detail_errors)
        write_html(page, path, title=name, details=details, index_href=index_href)
        for w in _page_warnings(page) + check_layout(page, dtype):
            problems.append(f"WARNING {fname}: {w}")
        for sid, (_t, dpage) in details.items():
            for w in _page_warnings(dpage) + check_layout(dpage, dtype):
                problems.append(f"WARNING {fname} (detail {sid}): {w}")
        pages.append(page)
        written.append(path)
        entries.append((name, fname, d.get("index_blurb")))
        for w in page.warnings:
            problems.append(f"WARNING {fname}: {w}")
    if entries:
        title = spec.get("title") if isinstance(spec, dict) else None
        write_index(entries, index_path, title=title or "Diagrams",
                    intro=_intro_of(spec) if isinstance(spec, dict) else None)
        written.append(index_path)
    return written, problems


_LAYOUT_PROBLEM = re.compile(r": (connectors cross|connectors share a line|connector '.*' runs "
                             r"through|labels overlap|label '.*' \(.*\) covers)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--index", dest="combined",
                    help="path for the index page (defaults to OUT/index.html)")
    ap.add_argument("--only", help="comma-separated diagram types to render")
    ap.add_argument("--draft", action="store_true",
                    help="exit 0 even if some elements have no description "
                         "(for early iterations; a finished set must not need this)")
    args = ap.parse_args()
    with open(args.spec, encoding="utf-8") as fh:
        spec = json.load(fh)
    only = set(t.strip().lower() for t in args.only.split(",")) if args.only else None
    written, problems = build(spec, args.out_dir, args.combined, only)
    for p in written:
        print(f"wrote {p}")
    for p in problems:
        print(p, file=sys.stderr)
    hard = [p for p in problems if not p.startswith("WARNING")]
    missing = [p for p in problems if "missing description:" in p]
    weak = [p for p in problems if "weak description:" in p]
    layout = [p for p in problems if _LAYOUT_PROBLEM.search(p)]
    if layout:
        print(f"\n{len(layout)} misleading connector(s) or label(s): crossings, lines through "
              "boxes, shared lines or covered labels. The router already avoided what it "
              "could; fix the rest in the spec (see SKILL.md step 6).", file=sys.stderr)
    if missing or weak:
        print(f"\n{len(missing)} element(s) without a description, {len(weak)} weak "
              "description(s). Every clickable element needs a description that says, "
              "in plain words, what job it does (see references/spec-format.md).",
              file=sys.stderr)
    if hard or (missing and not args.draft):
        if missing and not args.draft:
            print("Failing because descriptions are missing; pass --draft to allow "
                  "this while iterating.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
