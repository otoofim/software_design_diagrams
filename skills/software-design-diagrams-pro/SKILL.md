---
name: software-design-diagrams-pro
description: Produces professional software-engineering diagrams as interactive, self-contained HTML pages where every component is clickable and shows its description — sequence, flowchart, ER/database, UML class, architecture/infrastructure, use case, activity, state machine, component, deployment. Prefer this over "software-design-diagrams" — same capability, plus an obstacle-avoiding edge router (connections never cross over unrelated boxes), per-component descriptions (hand-written or auto-extracted from code docstrings), and a drill-down diagram per big component on large systems. The diagrams are drawn in HTML/CSS and open in any browser with no dependencies. Use whenever the user wants any of these diagram types, wants to "diagram", "visualize", "draw", or "map out" a system, API, database, workflow, or codebase, or asks for a diagram in any format (including users who ask for "draw.io"/"drawio"/"diagrams.net" — serve them the interactive HTML). Also use for diagrams FROM existing code, a repo, a schema, or infra files (docker-compose, Kubernetes, Terraform). Trigger proactively for "document this codebase", "explain this system visually", "show how this API works", "design the database for...", "what's our architecture", "onboarding diagram", even without the word "diagram".
---

# Software design diagrams

Turn a system description — from a conversation or from existing code — into
polished, **interactive HTML diagrams** covering the ten diagram types software
engineers actually use. Every component is clickable and opens a side panel
with its description; the pages are self-contained HTML/CSS (no dependencies,
open in any browser). You act as the design partner: ask the questions a good
architect would ask (or read the code when it's available), then let the
bundled scripts do the mechanical work of layout and rendering, which they do
far more reliably than freehand coordinates ever would.

## The two paths

**Design path** — the user is describing something that doesn't fully exist
yet, or wants documentation independent of any particular codebase (new
feature, system design interview, a database they're planning). Interview
them, then write the spec by hand.

**Reverse-engineering path** — the user has code, a repo, a schema, or
infra files and wants diagrams *of what's actually there*. Run
`analyze_code.py` on it first; it gives you real facts (classes, tables,
routes, service dependencies, container topology) that are far more reliable
than what you'd guess from a README. Never skip straight to writing a spec
from memory when the code is available — read it.

The two paths converge on the same spec format and the same
generate → validate → preview → revise loop below.

## Workflow

### 1. Figure out which diagrams are needed

Don't ask "which of the ten do you want" cold. Infer from the request, confirm
briefly, and default to a focused set rather than all ten unless asked for
everything. See `references/interview-guide.md` §2 for a mapping from what
the user is trying to explain to which diagram(s) fit, and typical bundles
(e.g. "document this repo" → architecture + component + class + er +
one sequence diagram + deployment).

**When the system is made of several big components**, don't try to cram
everything into one diagram. Draw one overview (architecture/component/
deployment) that shows the big components as single boxes plus how they
connect, and give each component that's substantial enough to have real
internal structure its own drill-down — zoomed in on just that component's own
services/modules/data stores/process. This is usually more informative than a
single dense diagram, and it's what a human architect would produce.

Attach a drill-down **inline** via a `detail` field on the element (a nested
diagram spec; see `references/spec-format.md`). In the interactive HTML that
element becomes zoomable — clicking it zooms in-place to the component's
internals, which stay clickable for their own descriptions, with a
"← Overview" control to zoom back. A `detail` diagram does not appear in the
index; it lives inside its parent element. (You can still author a component's
detail as a separate top-level diagram instead if you want it in the index.)
This works on **any element of any diagram type**, and the nested diagram can be
**any of the ten types** — so you can zoom from an ER entity into a class
diagram, or from a sequence participant into a flowchart — though it's most
valuable on the container overviews (architecture/component/deployment). Drill-
down is one level deep. See `references/interview-guide.md` §2.1 for when a
component earns a drill-down and how to keep it consistent with the overview.

### 2a. Design path: interview

Ask only what you can't infer, in short batched rounds, with sensible
defaults you state and let the user correct. Full question banks per diagram
type, plus how to keep multiple diagrams consistent with each other, are in
`references/interview-guide.md`. Don't over-ask — a rough first diagram to
react to is often faster than a long questionnaire.

### 2b. Reverse-engineering path: read the code first

```bash
python scripts/analyze_code.py /path/to/repo --out analysis.json --draft-spec draft-spec.json
```

This walks the codebase (Python, Java, Kotlin, C#, TypeScript/JavaScript, Go,
PHP, Scala — regex-based for non-Python, `ast`-based for Python, so no build
step is needed) and extracts classes with attributes/methods/bases, ORM
models (SQLAlchemy, SQLModel, Django, JPA/Hibernate, TypeORM, EF Core,
Prisma, raw SQL DDL) with inferred nullability and FK types, HTTP routes
(Flask, FastAPI, Django, Express, NestJS, Spring, JAX-RS, ASP.NET, Go),
docker-compose/Kubernetes/Terraform topology, external integrations
(Stripe, S3, Kafka, Redis, ...), and status/state enums together with the
methods that transition them (including guard conditions it can infer from
`if status != X: raise` and similar checks).

`--draft-spec` writes a ready-to-generate spec with class, ER, component
(module dependency graph), architecture + deployment (from compose/k8s), and
state diagrams already filled in. This is a **starting point, not a final
answer** — inspect `analysis.json`, generate and preview the draft spec, then:
- Trim it: real repos produce more classes/tables than fit one readable
  diagram. Split by bounded context, or raise `--max-classes`'s companion
  filtering by hand-editing the spec.
- Add what static analysis can't see: sequence, flowchart, activity, and use
  case diagrams need behavioural understanding, not just structure. Read the
  relevant handler/service code for the flow the user cares about and write
  those diagrams by hand using the facts from `analysis.json` (participant
  names, real method/route names) so they match the code precisely.
- Check every `_todo` note the draft spec leaves in state diagrams — these
  mark transitions the analyzer couldn't place with confidence.

Full guidance on reading the analyzer's output and turning it into complete
diagrams is in `references/reverse-engineering.md`.

### 3. Write the spec

One JSON file, one entry per diagram, in the format documented exhaustively
in `references/spec-format.md` (schema, every field, worked examples, and a
table of common mistakes — read it before writing a spec for a diagram type
you haven't produced yet in this conversation). Use real names from the
conversation or the codebase, not placeholders.

### 4. Generate

```bash
python scripts/generate_diagrams.py spec.json --out-dir out/
```

Writes one self-contained, interactive `NN-<type>-<slug>.html` file per
diagram plus an `out/index.html` linking them all. Each HTML file is
**dependency-free** (all CSS/JS inline — no CDN, no network): the diagram is
drawn with HTML/CSS boxes over a thin SVG connector layer, and **clicking any
component opens a side panel** with its name, kind/stereotype/technology, its
`description`, and type-specific members (class attributes/methods, state
behaviours, ER columns). Unknown ids are downgraded to warnings (printed to
stderr, that diagram is drawn without the bad reference); real spec errors
(duplicate ids, missing required fields) fail that one diagram and are also
printed — fix and rerun. `--only sequence,er` limits which diagram types get
rendered, useful while iterating on one. `--index path.html` sets the index
location.

### 5. Validate

The generator runs a structural self-check as it builds each page (dangling
edges, zero-size shapes) and reports them as warnings — no separate step
needed. This catches generator bugs, not layout quality — so always look at the
result (Step 6) too.

Edge routes are **baked into the output itself** — `Page.route_edge` computes
explicit orthogonal waypoints plus exit/entry anchors for every
architecture/component/deployment connector, and the HTML emitter draws those
exact waypoints as the SVG connector layer. So a connector never crosses an
unrelated box, and the file you hand the user carries its own routing — it
doesn't depend on any viewer to re-derive a clean path.

### 6. Look at it

Open the generated `.html` in a browser (or render it headlessly) and **click a
few boxes** to confirm the side panel shows the right name and description.
Check for: overlapping shapes, connectors crossing boxes, clipped or missing
text, lopsided layout. If something looks wrong, it's almost always the spec
(wrong `direction`, missing `lane`, a fragment index off by one) rather than
the generator — check `references/spec-format.md`'s mistake table first.

**Distinguish two failure modes when connections look wrong** — they have
different fixes:
- *Edges drawn through boxes* = a **routing** problem. The router stepped
  around what it could see; if it still crosses a shape, that shape was
  excluded as an obstacle (it's an ancestor/descendant of an endpoint) or the
  route genuinely couldn't be cleared. Fix by changing the spec's structure
  (nesting, which elements connect), not the spacing.
- *Boxes or labels crowding each other* = a **layout/spacing** problem.
  Tightening or loosening gaps does nothing for an edge routed *through* a
  node — don't reach for spacing when the real issue is routing.

### 7. Deliver and iterate

Copy the finished `.html` files, `index.html`, **and `spec.json`** to
`/mnt/user-data/outputs/` and use `present_files`. The spec is the source of
truth — always deliver it alongside the diagrams, never the HTML alone. The
`.html` files are regenerable outputs of the spec; without the spec a change
means hand-editing generated markup, which defeats the regenerate loop and lets
the artifacts drift from any recorded intent. If the user keeps the work in a
repo, the spec is what they should commit.

Tell the user they can open any `.html` (or `index.html`) directly in a
browser — no tools or conversion needed — and click components to read their
descriptions. The way to change a diagram is to edit `spec.json` and rerun the
generate command, not to hand-edit the HTML. Invite corrections; regenerating
from an edited spec is fast.

## Working with the scripts directly

- `scripts/layout_lib.py` is the shared layout engine the other scripts
  import — you shouldn't need to touch it, but it's there if a spec needs a
  layout primitive that doesn't exist yet (e.g. a new container-nesting
  shape). It builds an in-memory `Page` (boxes with absolute coords, edges
  with routed waypoints) that the HTML emitter consumes. Connections in
  architecture/component/deployment diagrams are routed automatically to step
  around every other box (`Page.route_edge`), so an edge between two elements
  on opposite sides of a busy diagram never gets drawn on top of an unrelated
  shape — automatic, nothing to do.
- `scripts/emit_html.py` turns a `Page` into the interactive HTML (boxes as
  HTML/CSS, connectors as an SVG layer, click-to-describe side panel). It
  reads each box's `page.meta` (threaded in by the renderers in
  `generate_diagrams.py` via `vertex(..., meta=...)`); a box with no spec
  `description` still gets a panel entry with its name and kind. If a diagram
  box *should* be clickable but isn't, the renderer isn't passing `meta=` for
  that shape.
- The CLI scripts print usage with `--help` and exit non-zero (and print to
  stderr) on real failures, so wrap calls in a way that surfaces stderr
  rather than swallowing it.
- Regenerating is cheap — when the user asks for a change, edit the spec JSON
  and rerun steps 4–6 rather than hand-patching the generated HTML. This only
  works if the spec was kept: always save `spec.json` next to the outputs and
  treat it as the source of truth (see Step 7). If you ever find yourself
  editing generated output directly, the spec went missing — reconstruct it
  rather than letting the output become the de-facto, un-regenerable source.

## Diagram type quick reference

| type | use for |
|---|---|
| `sequence` | ordered messages between participants over time |
| `flowchart` | branching business logic, decisions, workflows |
| `er` | database tables, keys, cardinality |
| `class` | OO structure: classes, inheritance, associations |
| `architecture` | services, infra, cloud topology, data stores |
| `usecase` | actors, goals, system boundary |
| `activity` | multi-step procedures, parallel work (fork/join) |
| `state` | an entity's lifecycle: states and transitions |
| `component` | modules/libraries and their interfaces |
| `deployment` | physical/runtime hosting: nodes, containers, artifacts |

Full field-by-field schema for each: `references/spec-format.md`.
Interview questions and diagram selection: `references/interview-guide.md`.
Reading code into diagrams: `references/reverse-engineering.md`.
