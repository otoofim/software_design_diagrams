#!/usr/bin/env python3
"""
emit_html.py - turn a laid-out `Page` (see layout_lib.py) into a single,
self-contained interactive HTML page.

The diagram is drawn with HTML + CSS: every shape is an absolutely-positioned
`<div>` placed at the coordinates the layout engine already computed
(`page.abs`), and connectors are a single inline `<svg>` overlay *behind* the
boxes, driven by the exact orthogonal waypoints + exit/entry anchors the
obstacle-avoiding router produced, so a connector never crosses an unrelated
box.

Every component box is clickable: a side panel shows the component's name,
kind/stereotype/technology, its free-text `description` (from the spec, or
auto-extracted from code), and any type-specific members (class attributes /
methods, state entry/do/exit, ER columns). The output has **no external
dependencies** — all CSS and JS are inline, so it opens in any browser offline.

Usage (normally invoked via generate_diagrams.py):
    from emit_html import write_html, write_index
"""
from __future__ import annotations

import html
import json
import os
import re

# FONT_SIZE is the metric the box-fitting heuristics in layout_lib assume; the
# HTML boxes must use the same font size (and a metric-compatible sans font) or
# text will overflow or leave slack. See the plan's "text sizing" risk.
FONT_SIZE = 12


# --------------------------------------------------------------------------- style

def style_dict(s):
    out = {}
    for part in (s or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v
        elif part:
            out[part] = True
    return out


def colour(v, default):
    if not v or v in ("none", "default", "inherit"):
        return default
    return v


def plain(label):
    """Collapse an html=1 label down to plain text (used for tooltips / panel
    titles where we don't want markup)."""
    t = re.sub(r"<br\s*/?>|<hr[^>]*>|</div>", "\n", label or "")
    t = re.sub(r"<[^>]+>", "", t)
    return html.unescape(t).replace("\xa0", " ").strip()


def esc_attr(s):
    return html.escape(str(s or ""), quote=True)


# --------------------------------------------------------------------------- geometry

def _depth(cid, cell_by_id, cache):
    """Nesting depth of a cell (distance to root cell '1'), for z-index so a
    child box always paints above its container."""
    if cid in cache:
        return cache[cid]
    c = cell_by_id.get(cid)
    if c is None:
        return 0
    p = c.parent
    d = 0 if p in (None, "0", "1") else 1 + _depth(p, cell_by_id, cache)
    cache[cid] = d
    return d


def _clip(box, px, py):
    """Point on `box`'s border on the ray toward (px, py), used when an edge
    end has no explicit anchor."""
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    dx, dy = px - cx, py - cy
    if dx == 0 and dy == 0:
        return cx, cy
    t = min((w / 2) / abs(dx) if dx else 1e9, (h / 2) / abs(dy) if dy else 1e9)
    return cx + dx * t, cy + dy * t


# --------------------------------------------------------------------------- shapes

def _shape_css(st, w, h):
    """Return (css_props, extra_class) for a shape's visual form. Unknown
    shapes fall back to a plain rectangle."""
    shape = st.get("shape", "")
    props, cls = [], ""
    if "ellipse" in st or shape in ("endState", "startState", "sumEllipse"):
        props.append("border-radius:50%")
        cls = "shape-ellipse"
    elif "rhombus" in st:
        cls = "shape-rhombus"  # clip-path handled in CSS
    elif shape == "cylinder3":
        cls = "shape-cylinder"
    elif shape == "cube":
        cls = "shape-cube"
    elif shape == "note":
        cls = "shape-note"
    elif st.get("rounded") == "1":
        props.append("border-radius:8px")
    return props, cls


# --------------------------------------------------------------------------- boxes

def _box_divs(page, ox=0.0, oy=0.0, detail_ids=None):
    """Yield an HTML <div> string for every vertex cell, back-to-front
    (containers before leaves) so nesting reads correctly. Boxes whose spec id
    is in `detail_ids` are marked zoomable (clicking them opens a drill-down
    overlay instead of the description panel)."""
    # detail_ids: a set of zoomable spec ids, or a {spec_id: layer_key} map
    # when the layer keys differ from the ids (nested drill-downs).
    detail_ids = detail_ids or set()
    if not isinstance(detail_ids, dict):
        detail_ids = {str(k): str(k) for k in detail_ids}
    cell_by_id = {c.id: c for c in page.cells}
    depth_cache: dict = {}
    verts = []
    for c in page.cells:
        if not c.is_vertex:
            continue
        if c.relative:
            continue  # edge labels etc.
        cid = c.id
        if cid not in page.abs:
            continue
        verts.append(c)
    # paint shallow (containers) first; deeper boxes get a higher z-index
    verts.sort(key=lambda c: _depth(c.id, cell_by_id, depth_cache))

    divs = []
    for c in verts:
        cid = c.id
        x, y, w, h = page.abs[cid]
        st = style_dict(c.style)
        if "edgeLabel" in st:
            continue
        fill = colour(st.get("fillColor"), "#ffffff")
        stroke = colour(st.get("strokeColor"), "#64748b")
        depth = _depth(cid, cell_by_id, depth_cache)
        is_container = st.get("container") == "1" or "swimlane" in st
        dashed = st.get("dashed") == "1"
        shape_props, shape_cls = _shape_css(st, w, h)

        classes = ["box"]
        if is_container:
            classes.append("container")
        if shape_cls:
            classes.append(shape_cls)
        if dashed:
            classes.append("dashed")
        meta = page.meta.get(cid)
        spec_id = meta.get("spec_id") if meta else None
        has_detail = spec_id is not None and str(spec_id) in detail_ids
        if meta:
            classes.append("clickable")
        if has_detail:
            classes.append("zoomable")

        # label placement: container/swimlane titles and verticalAlign=top sit
        # at the top; plain nodes centre their label.
        va_top = (st.get("verticalAlign") == "top" or is_container
                  or st.get("container") == "1")
        label_pos = "bottom" if st.get("verticalLabelPosition") == "bottom" else (
            "top" if va_top else "center")
        align = "left" if st.get("align") == "left" else "center"

        style_props = [
            f"left:{x + ox:.0f}px", f"top:{y + oy:.0f}px",
            f"width:{w:.0f}px", f"height:{h:.0f}px",
            f"z-index:{depth + 1}",
            f"--fill:{fill}", f"--stroke:{stroke}",
        ] + shape_props

        attrs = [
            f'class="{" ".join(classes)}"',
            f'style="{";".join(style_props)}"',
            f'data-cid="{esc_attr(cid)}"',
            f'data-label-pos="{label_pos}"',
            f'data-align="{align}"',
        ]
        if has_detail:
            attrs.append(f'data-detail="{esc_attr(detail_ids[str(spec_id)])}"')
        if meta:
            attrs += ['tabindex="0"', 'role="button"']
            title = plain(meta.get("title") or "")
            tip = title
            if has_detail:
                tip = f"{title} — click to zoom in" if title else "click to zoom in"
            elif meta.get("description"):
                tip = plain(meta["description"])
            if tip:
                attrs.append(f'title="{esc_attr(tip)}"')
            attrs.append(f'aria-label="{esc_attr(title or cid)}"')

        # the stored value is already escaped html=1 markup; render it as-is
        inner = c.value or ""
        divs.append(f'<div {" ".join(attrs)}><div class="box-label">{inner}</div></div>')
    return divs


# --------------------------------------------------------------------------- edges

def _edge_geo(page, c):
    """Return the full point list [start, *waypoints, end] for an edge cell,
    honouring exit/entry anchors, or None if the endpoints can't be resolved."""
    st = style_dict(c.style)
    pts = list(c.points or [])
    sp = tuple(c.source_point) if c.source_point else None
    tp = tuple(c.target_point) if c.target_point else None
    s, t = c.source, c.target
    sb = page.abs.get(s) if s else None
    tb = page.abs.get(t) if t else None
    if sb and tb:
        scx, scy = sb[0] + sb[2] / 2, sb[1] + sb[3] / 2
        tcx, tcy = tb[0] + tb[2] / 2, tb[1] + tb[3] / 2
        if "exitX" in st and "exitY" in st:
            sp = (sb[0] + sb[2] * float(st["exitX"]), sb[1] + sb[3] * float(st["exitY"]))
        else:
            sp = _clip(sb, *(pts[0] if pts else (tcx, tcy)))
        if "entryX" in st and "entryY" in st:
            tp = (tb[0] + tb[2] * float(st["entryX"]), tb[1] + tb[3] * float(st["entryY"]))
        else:
            tp = _clip(tb, *(pts[-1] if pts else (scx, scy)))
    if sp is None or tp is None:
        return None, st
    return [sp] + pts + [tp], st


def _longest_segment_mid(path):
    """Midpoint of the longest axis-aligned segment — a calmer spot for an edge
    label than the geometric middle of the whole polyline."""
    best, blen = (path[0], path[-1]), -1.0
    for a, b in zip(path, path[1:]):
        d = abs(a[0] - b[0]) + abs(a[1] - b[1])
        if d > blen:
            best, blen = (a, b), d
    return ((best[0][0] + best[1][0]) / 2, (best[0][1] + best[1][1]) / 2)


_LABEL_FONT_PX = 11
_LABEL_CHAR_W = 6.2  # matches .edge-label: 11px sans


def _label_rect(text, x, y):
    lines = text.splitlines() or [""]
    w = max(len(t) for t in lines) * _LABEL_CHAR_W + 8
    h = 16 * len(lines)
    return (x - w / 2, y - h / 2, w, h)


def _rects_hit(a, b, slack=1.0):
    return (a[0] < b[0] + b[2] - slack and b[0] < a[0] + a[2] - slack and
            a[1] < b[1] + b[3] - slack and b[1] < a[1] + a[3] - slack)


def place_edge_labels(page):
    """Choose a spot for every edge label: on its own connector, clear of
    every box and of labels already placed. Candidates are tried from the
    middle of the longest segment outwards, so a label stays near the centre
    of its line when it can. Returns {edge id: (x, y, text, rect)}; the
    layout check reports any label that found no clear spot."""
    boxes = [page.abs[c.id] for c in page.cells
             if c.is_vertex and not c.relative and c.id in page.abs]
    leaves = [b for b in boxes if not any(o is not b and _contains(b, o) for o in boxes)]
    placed, out = [], {}
    for c in page.cells:
        if not c.is_edge:
            continue
        text = plain(c.value)
        if not text:
            continue
        path, _st = _edge_geo(page, c)
        if not path:
            continue
        segs = sorted(zip(path, path[1:]),
                      key=lambda ab: -(abs(ab[0][0] - ab[1][0]) + abs(ab[0][1] - ab[1][1])))
        cands = []
        for a, b in segs:
            for f in (0.5, 0.35, 0.65, 0.25, 0.75):
                cands.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
        best = None
        for x, y in cands:
            r = _label_rect(text, x, y)
            if not any(_rects_hit(r, b) for b in leaves) and not any(_rects_hit(r, q) for q in placed):
                best = (x, y, r)
                break
        if best is None:
            x, y = _longest_segment_mid(path)
            best = (x, y, _label_rect(text, x, y))
        placed.append(best[2])
        out[c.id] = (best[0], best[1], text, best[2])
    return out


def _contains(outer, inner, pad=0.5):
    return (outer[0] - pad <= inner[0] and outer[1] - pad <= inner[1] and
            inner[0] + inner[2] <= outer[0] + outer[2] + pad and
            inner[1] + inner[3] <= outer[1] + outer[3] + pad)


def _edges_svg_and_labels(page, ox=0.0, oy=0.0):
    """Return (list of svg element strings, list of label div strings)."""
    svg_parts, labels = [], []
    label_at = place_edge_labels(page)
    for c in page.cells:
        if not c.is_edge:
            continue
        geo = _edge_geo(page, c)
        path, st = geo
        if path is None:
            continue
        dashed = ' class="edge dashed"' if st.get("dashed") == "1" else ' class="edge"'
        no_arrow = st.get("endArrow") == "none"
        open_arrow = st.get("endFill") == "0" or st.get("endArrow") in ("open", "openThin")
        marker = "" if no_arrow else (
            ' marker-end="url(#arrow-open)"' if open_arrow else ' marker-end="url(#arrow)"')
        start_marker = ' marker-start="url(#arrow)"' if st.get("startArrow") not in (None, "none") else ""
        pts = " ".join(f"{p[0] + ox:.1f},{p[1] + oy:.1f}" for p in path)
        svg_parts.append(f'<polyline points="{pts}"{dashed}{marker}{start_marker} fill="none"/>')
        if c.id in label_at:
            mx, my, lbl, _r = label_at[c.id]
            labels.append(
                f'<div class="edge-label" style="left:{mx + ox:.0f}px;top:{my + oy:.0f}px">'
                f'{html.escape(lbl)}</div>')
    return svg_parts, labels


# --------------------------------------------------------------------------- meta

def _panel_payload(page):
    """Serialisable {cid: meta} for the click-to-describe side panel."""
    out = {}
    for cid, m in page.meta.items():
        out[cid] = {
            "title": plain(m.get("title") or "") or cid,
            "description": (m.get("description") or "").strip(),
            "technology": m.get("technology") or "",
            "stereotype": m.get("stereotype") or "",
            "kind": m.get("kind") or "",
            "members": [x for x in (m.get("members") or []) if x],
        }
    return out


# --------------------------------------------------------------------------- template

_CSS = """
:root{
  --bg:#f8fafc; --canvas:#ffffff; --ink:#0f172a; --muted:#64748b;
  --panel:#ffffff; --panel-border:#e2e8f0; --edge:#475569; --accent:#2563eb;
  --shadow:0 1px 2px rgba(15,23,42,.06),0 2px 8px rgba(15,23,42,.08);
  --font:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
}
@media (prefers-color-scheme:dark){
  :root{--bg:#0b1120;--canvas:#0f172a;--ink:#e2e8f0;--muted:#94a3b8;
    --panel:#111827;--panel-border:#1f2937;--edge:#94a3b8;--accent:#60a5fa;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 2px 10px rgba(0,0,0,.5);}
}
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{background:var(--bg);color:var(--ink);font-family:var(--font);font-size:13px}
.topbar{position:sticky;top:0;z-index:50;display:flex;align-items:center;gap:12px;
  padding:10px 16px;background:var(--panel);border-bottom:1px solid var(--panel-border)}
.topbar h1{font-size:15px;margin:0;font-weight:600}
.topbar .hint{color:var(--muted);font-size:12px}
.topbar a{color:var(--accent);text-decoration:none;font-size:12px}
.home-link{border:1px solid var(--panel-border);border-radius:6px;padding:4px 10px;
  color:var(--accent);background:var(--bg);white-space:nowrap}
.home-link:hover{border-color:var(--accent)}
.stage{position:relative;overflow:auto;height:calc(100vh - 42px)}
.intro{margin:20px 24px 0;max-width:1100px;background:var(--panel);border:1px solid var(--panel-border);
  border-radius:8px;padding:14px 18px;box-shadow:var(--shadow);line-height:1.55;font-size:13.5px}
.intro summary{cursor:pointer;font-weight:600;font-size:14px;color:var(--accent)}
.intro p{margin:8px 0}
.intro h3{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin:14px 0 4px}
.intro ol,.intro ul{margin:4px 0;padding-left:22px}
.intro li{margin:3px 0}
.intro code{background:var(--bg);border:1px solid var(--panel-border);border-radius:3px;padding:0 3px;font-size:12px}
.intro dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;margin:4px 0}
.intro dt{font-weight:600}.intro dd{margin:0}
.canvas{position:relative;margin:24px;background:var(--canvas)}
.edges{position:absolute;left:0;top:0;overflow:visible;pointer-events:none;z-index:0}
.edge{stroke:var(--edge);stroke-width:1.4;stroke-linejoin:round;stroke-linecap:round}
.edge.dashed{stroke-dasharray:6 4}
.edge-label{position:absolute;transform:translate(-50%,-50%);z-index:40;
  background:var(--canvas);color:var(--muted);padding:0 4px;border-radius:3px;
  font-size:11px;white-space:nowrap;pointer-events:none}
.box{position:absolute;background:var(--fill);border:1.4px solid var(--stroke);
  color:#0f172a;border-radius:4px;overflow:hidden;font-size:12px;line-height:1.35;
  display:flex;align-items:center;justify-content:center;text-align:center}
.box .box-label{padding:4px 6px;width:100%;overflow:hidden}
.box[data-label-pos="bottom"]{overflow:visible;border-radius:50% 50% 4px 4px}
.box[data-label-pos="bottom"] .box-label{position:absolute;top:100%;left:50%;transform:translateX(-50%);
  width:max-content;max-width:160px;padding-top:4px;font-weight:600}
.box[data-label-pos="top"]{align-items:flex-start}
.box[data-label-pos="top"] .box-label{text-align:inherit}
.box[data-align="left"] .box-label{text-align:left}
.box.container{background:color-mix(in srgb,var(--fill) 55%,transparent);align-items:flex-start}
.box.dashed{border-style:dashed}
.box.shape-ellipse{border-radius:50%}
.box.shape-rhombus{background:transparent;border:none}
.box.shape-rhombus::before{content:"";position:absolute;inset:0;background:var(--fill);
  border:1.4px solid var(--stroke);transform:rotate(45deg) scale(.72)}
.box.shape-rhombus .box-label{position:relative;z-index:1}
.box.shape-cylinder{border-radius:50%/14px}
.box.shape-note{border-radius:0;z-index:30!important}
.box.clickable{cursor:pointer}
.box.clickable:hover{box-shadow:var(--shadow);filter:brightness(1.02)}
.box.clickable:focus-visible,.box.selected{outline:2px solid var(--accent);outline-offset:1px}
.box.zoomable{cursor:zoom-in}
.box.zoomable::after{content:"\\2922";position:absolute;top:3px;right:5px;font-size:12px;
  line-height:1;color:var(--accent);opacity:.8;pointer-events:none}
.box.zoomable:hover{box-shadow:0 0 0 2px var(--accent),var(--shadow)}
.detail-panel{position:fixed;top:42px;right:0;width:320px;max-width:85vw;
  height:calc(100vh - 42px);background:var(--panel);border-left:1px solid var(--panel-border);
  box-shadow:var(--shadow);padding:18px;overflow:auto;transform:translateX(100%);
  transition:transform .18s ease;z-index:60}
.detail-panel.open{transform:translateX(0)}
@media (prefers-reduced-motion:reduce){.detail-panel{transition:none}}
.detail-panel h2{margin:0 0 4px;font-size:16px}
.detail-panel .badges{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
.badge{font-size:11px;padding:2px 8px;border-radius:999px;background:var(--bg);
  border:1px solid var(--panel-border);color:var(--muted)}
.detail-panel .desc{margin:12px 0;white-space:pre-wrap;line-height:1.5}
.detail-panel .desc.empty{color:var(--muted);font-style:italic}
.detail-panel h3{font-size:12px;text-transform:uppercase;letter-spacing:.04em;
  color:var(--muted);margin:16px 0 6px}
.detail-panel ul{margin:0;padding-left:18px}
.detail-panel li{margin:2px 0;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.detail-panel .close{position:absolute;top:12px;right:12px;border:none;background:none;
  font-size:20px;line-height:1;cursor:pointer;color:var(--muted)}
/* drill-down zoom overlay */
.zoom-layer[hidden]{display:none}  /* must beat the display:flex below */
.zoom-layer{position:fixed;top:42px;left:0;right:0;bottom:0;background:var(--bg);z-index:55;
  display:flex;flex-direction:column;opacity:0;transform:scale(.96);
  transition:opacity .2s ease,transform .2s ease}
.zoom-layer.open{opacity:1;transform:scale(1)}
@media (prefers-reduced-motion:reduce){.zoom-layer{transition:none}}
.zoom-head{display:flex;align-items:center;gap:14px;padding:10px 16px;
  background:var(--panel);border-bottom:1px solid var(--panel-border)}
.zoom-head h2{margin:0;font-size:15px;font-weight:600}
.zoom-back{border:1px solid var(--panel-border);background:var(--bg);color:var(--accent);
  border-radius:6px;padding:5px 12px;font-size:13px;cursor:pointer}
.zoom-back:hover{border-color:var(--accent)}
.zoom-stage{flex:1;min-height:0}
"""

_JS = """
(function(){
  var cssEsc = (window.CSS && window.CSS.escape)
    ? function(s){return window.CSS.escape(s);}
    : function(s){return String(s).replace(/[^a-zA-Z0-9_-]/g, function(c){return "\\\\"+c;});};
  var DATA = JSON.parse(document.getElementById("diagram-meta").textContent);
  var DETAILS = DATA.details || {};
  var panel = document.getElementById("panel");
  var mainStage = document.getElementById("main-stage");
  var selected = null;
  var zoomStack = [];  // ids of detail layers currently open (supports nesting)
  function esc(s){var d=document.createElement("div");d.textContent=s==null?"":s;return d.innerHTML;}
  function activeMeta(){
    // metadata for whichever layer is on top: a detail layer, else the main one
    if(zoomStack.length){ return DETAILS[zoomStack[zoomStack.length-1]] || {}; }
    return DATA.main || {};
  }
  function scope(){
    if(zoomStack.length){
      return document.querySelector('.zoom-layer[data-detail-id="'+cssEsc(zoomStack[zoomStack.length-1])+'"]');
    }
    return mainStage;
  }
  function open(cid){
    var m = activeMeta()[cid]; if(!m) return;
    if(selected) selected.classList.remove("selected");
    var el = scope().querySelector('.box[data-cid="'+cssEsc(cid)+'"]');
    if(el){ el.classList.add("selected"); selected = el; }
    var badges = [];
    if(m.kind) badges.push('<span class="badge">'+esc(m.kind)+'</span>');
    if(m.stereotype) badges.push('<span class="badge">\\u00ab'+esc(m.stereotype)+'\\u00bb</span>');
    if(m.technology) badges.push('<span class="badge">'+esc(m.technology)+'</span>');
    var members = (m.members||[]).map(function(x){return '<li>'+esc(x)+'</li>';}).join("");
    var desc = m.description
      ? '<div class="desc">'+esc(m.description)+'</div>'
      : '<div class="desc empty">No description provided.</div>';
    panel.innerHTML =
      '<button class="close" aria-label="Close">\\u00d7</button>'+
      '<h2>'+esc(m.title)+'</h2>'+
      (badges.length?'<div class="badges">'+badges.join("")+'</div>':'')+
      desc+
      (members.length?'<h3>Members</h3><ul>'+members+'</ul>':'');
    panel.classList.add("open");
    panel.querySelector(".close").onclick = close;
  }
  function close(){
    panel.classList.remove("open");
    if(selected){selected.classList.remove("selected");selected=null;}
  }
  function zoomIn(detailId){
    var layer = document.querySelector('.zoom-layer[data-detail-id="'+cssEsc(detailId)+'"]');
    if(!layer) return;
    close();
    layer.hidden = false;
    // next frame so the transition runs
    requestAnimationFrame(function(){ layer.classList.add("open"); });
    zoomStack.push(detailId);
  }
  function zoomOut(){
    var id = zoomStack.pop();
    if(id == null) return;
    close();
    var layer = document.querySelector('.zoom-layer[data-detail-id="'+cssEsc(id)+'"]');
    if(layer){
      layer.classList.remove("open");
      var done = function(){ layer.hidden = true; layer.removeEventListener("transitionend", done); };
      layer.addEventListener("transitionend", done);
      setTimeout(done, 240);  // fallback if no transition fires
    }
  }
  document.addEventListener("click", function(e){
    if(e.target.closest(".zoom-back")){ zoomOut(); e.stopPropagation(); return; }
    var box = e.target.closest(".box.clickable");
    if(box){
      var det = box.getAttribute("data-detail");
      if(det && DETAILS[det]){ zoomIn(det); }
      else { open(box.getAttribute("data-cid")); }
      e.stopPropagation(); return;
    }
    if(!e.target.closest("#panel")) close();
  });
  document.addEventListener("keydown", function(e){
    if(e.key === "Escape"){ if(panel.classList.contains("open")) close(); else zoomOut(); return; }
    var box = e.target.closest && e.target.closest(".box.clickable");
    if(box && (e.key === "Enter" || e.key === " ")){
      e.preventDefault();
      var det = box.getAttribute("data-detail");
      if(det && DETAILS[det]){ zoomIn(det); } else { open(box.getAttribute("data-cid")); }
    }
  });
})();
"""


_SVG_DEFS = ('<defs>'
             '<marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" '
             'orient="auto" markerUnits="userSpaceOnUse">'
             '<path d="M0,0 L8,3 L0,6 z" fill="var(--edge)"/></marker>'
             '<marker id="arrow-open" markerWidth="12" markerHeight="12" refX="8" refY="3" '
             'orient="auto" markerUnits="userSpaceOnUse">'
             '<path d="M0,0 L8,3 L0,6" fill="none" stroke="var(--edge)" stroke-width="1.2"/></marker>'
             '</defs>')


def _canvas_block(page, detail_ids=None):
    """The inner diagram markup (sized canvas with its SVG edge layer + boxes +
    edge labels) plus this page's meta payload. Shared by the top-level page and
    every drill-down detail view so they render identically."""
    x0, y0, x1, y1 = page.bounds()
    # Normalise coords into a 0,0-based canvas with a small margin by baking the
    # offset into box and connector coords — boxes and the SVG overlay then
    # share one frame, so connectors stay pinned to their boxes (and diagrams
    # with a negative origin render correctly).
    pad = 24
    ox, oy = -x0 + pad, -y0 + pad
    cw, chh = x1 - x0 + 2 * pad, y1 - y0 + 2 * pad
    boxes = _box_divs(page, ox, oy, detail_ids)
    svg_parts, labels = _edges_svg_and_labels(page, ox, oy)
    svg = (f'<svg class="edges" width="{cw:.0f}" height="{chh:.0f}" '
           f'viewBox="0 0 {cw:.0f} {chh:.0f}">' + _SVG_DEFS + "".join(svg_parts) + "</svg>")
    canvas = (f'<div class="canvas" style="width:{cw:.0f}px;height:{chh:.0f}px">'
              + svg + "".join(boxes) + "".join(labels) + "</div>")
    return canvas, _panel_payload(page)


def _inline(text):
    """Escape, then turn `code` spans into <code>."""
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", html.escape(str(text)))
    out = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", out)
    return re.sub(r"(?<![\w*])\*([^*\s][^*]*?)\*(?![\w*])", r"<i>\1</i>", out)


def render_intro(intro, heading="About this diagram"):
    """Visible explanation shown above a diagram (summary / how to read / key
    points / glossary). `intro` is a dict taken from the diagram spec; returns ''
    when it carries nothing."""
    if not intro:
        return ""
    parts = []
    for para in ([intro["summary"]] if isinstance(intro.get("summary"), str) else intro.get("summary") or []):
        parts.append(f"<p>{_inline(para)}</p>")
    for key, section, tag in (("how_to_read", "How to read this diagram", "ul"),
                              ("steps", "Step by step", "ol"),
                              ("key_points", "Things worth knowing", "ul")):
        items = intro.get(key) or []
        if items:
            lis = "".join(f"<li>{_inline(i)}</li>" for i in items)
            parts.append(f"<h3>{section}</h3><{tag}>{lis}</{tag}>")
    gl = intro.get("glossary") or {}
    if gl:
        rows = "".join(f"<dt>{_inline(k)}</dt><dd>{_inline(v)}</dd>" for k, v in gl.items())
        parts.append(f"<h3>Terms used here</h3><dl>{rows}</dl>")
    if not parts:
        return ""
    return (f'<details class="intro" open><summary>{html.escape(heading)} (click to collapse)</summary>'
            + "".join(parts) + "</details>")


def render_page_html(page, title=None, details=None, index_href=None):
    """Full standalone HTML document string for one Page.

    `index_href` (optional): relative URL of the index page; when given, a
    "Home" link is shown in the top bar so a reader can get back to it.

    `details` (optional): {spec_id: (detail_title, detail_Page)} — components
    whose spec id is a key become zoomable; clicking one opens an in-place zoom
    overlay showing that component's own diagram (its internal process /
    subcomponents), which stay clickable for their own descriptions.
    """
    details = details or {}
    # top-level zoomables are the keys without a path separator; a nested key
    # "a/b" is zoomable from inside layer "a" (box spec id "b").
    detail_ids = {k: k for k in details if "/" not in k}
    canvas, payload = _canvas_block(page, detail_ids)
    name = html.escape(title or page.name or "Diagram")
    hint = ("Click a component to see its description"
            + (" — boxes with a ⤢ zoom in" if detail_ids else ""))
    home_link = (f'<a class="home-link" href="{esc_attr(index_href)}">← Home</a>'
                 if index_href else "")

    # one detail layer per zoomable component; hidden until its box is clicked
    detail_blocks, detail_payload = [], {}
    for sid, (dtitle, dpage) in details.items():
        children = {k.rsplit("/", 1)[1]: k for k in details
                    if "/" in k and k.rsplit("/", 1)[0] == str(sid)}
        dcanvas, dmeta = _canvas_block(dpage, children)
        detail_payload[str(sid)] = dmeta
        detail_blocks.append(
            f'<section class="zoom-layer" data-detail-id="{esc_attr(sid)}" hidden>'
            f'<div class="zoom-head"><button class="zoom-back" type="button">{"← Back" if "/" in str(sid) else "← Overview"}</button>'
            f'<h2>{html.escape(dtitle)}</h2></div>'
            f'<div class="stage zoom-stage">{render_intro(getattr(dpage, "intro", None))}{dcanvas}</div></section>')

    # Escape '<' so any "</script>" in the data can't break out of the blob.
    meta_json = json.dumps({"main": payload, "details": detail_payload},
                           ensure_ascii=False).replace("<", "\\u003c")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="topbar">
  {home_link}
  <h1>{name}</h1>
  <span class="hint">{hint}</span>
</div>
<div class="stage" id="main-stage">
  {render_intro(getattr(page, "intro", None))}
  {canvas}
</div>
{"".join(detail_blocks)}
<aside class="detail-panel" id="panel" aria-live="polite" aria-label="Component details"></aside>
<script type="application/json" id="diagram-meta">{meta_json}</script>
<script>{_JS}</script>
</body>
</html>
"""


def write_html(page, path, title=None, details=None, index_href=None):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_page_html(page, title, details, index_href))
    return path


def write_index(entries, path, title="Diagrams", intro=None):
    """entries: list of (display_name, filename). Writes a simple, dependency-free
    index page linking every diagram."""
    items = "\n".join(
        f'    <li><a href="{esc_attr(e[1])}"><b>{html.escape(e[0])}</b>'
        + (f'<span class="blurb">{_inline(e[2])}</span>' if len(e) > 2 and e[2] else "")
        + '</a></li>'
        for e in entries)
    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>{_CSS}
.index{{max-width:720px;margin:40px auto;padding:0 20px}}
.index ul{{list-style:none;padding:0}}
.index li{{margin:0}}
.index a{{display:block;padding:14px 16px;margin:8px 0;background:var(--panel);
  border:1px solid var(--panel-border);border-radius:8px;color:var(--ink);
  text-decoration:none;font-size:15px;box-shadow:var(--shadow)}}
.index a:hover{{border-color:var(--accent);color:var(--accent)}}
.index .blurb{{display:block;color:var(--muted);font-size:13px;margin-top:4px;line-height:1.5}}
.index .intro{{margin:0 0 16px}}
</style>
</head>
<body>
<div class="index">
  <h1>{html.escape(title)}</h1>
  {render_intro(intro, "About this guide")}
  <ul>
{items}
  </ul>
</div>
</body>
</html>
"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return path
