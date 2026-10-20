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

## Who the diagrams are for

Write every diagram set for a **newcomer**: an engineer who has never seen
the project, opens `index.html`, and reads in order. By the end they should
know what the project does, how data moves through it, where each piece lives
in the code, and why the non-obvious pieces exist. Concretely:

- **Every clickable element has a `description`** — at every drill-down
  depth, groups and component-diagram interfaces included. The generator
  **fails (exit 1)** when any is missing; `--draft` relaxes this only while
  iterating.
- **A description says the job the component does**, in plain words: what it
  is *for*, not how its code is wired. One sentence for the job, plus at most
  one sentence on why it exists or the behaviour a newcomer would trip over.
  Class names, library calls, function signatures and config keys go in
  `technology` (or the diagram's `key_points`), never as the description.
  The generator flags descriptions that start with a code name, are mostly
  identifiers, walk through function calls, run past ~320 characters, or
  just repeat the label (`weak description` warnings). See "Writing
  descriptions" below.
- **Every diagram name ends with its type** ("Read path — Architecture
  diagram", "Checkout — Sequence diagram"). The generator appends it if
  missing, so titles, index entries and file names all say what kind of
  diagram they are.
- **Every diagram has a `summary` and `how_to_read`**, plus a `glossary`
  when it uses domain terms; `index_blurb`s turn the index into a reading
  path (context → overview → drill-downs → flows → data → deployment).
- **Labels use plain words** a newcomer understands; exact code names go in
  `technology`.
- **No connector misleads.** Connectors don't cross each other, don't run
  through a box that isn't one of their ends, don't share a stretch of line
  with an unrelated connector, and their labels don't sit on boxes or on each
  other. Each one makes a reader follow the wrong line. The generator routes
  around all of these and reports what it couldn't avoid; a finished set has
  none left, or only ones you've checked and can justify.

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
"← Overview" / "← Back" control to zoom out. A `detail` diagram does not
appear in the index; it lives inside its parent element. (You can still author
a component's detail as a separate top-level diagram instead if you want it in
the index.) Two rules:

- **A drill-down is the same diagram type as its parent** — an architecture
  box zooms into an architecture diagram, a flowchart step into a flowchart,
  a class into a class diagram, for all ten types. A mismatch is a hard error.
  A *different* view of a component (its request flow, its classes) is its
  own top-level diagram, referenced from the box's description.
- **Drill-downs are recursive, until atomic.** A box inside a drill-down can
  carry its own `detail`, to any depth. Keep drilling every box until it can
  no longer be broken down into smaller components or steps; external
  systems are always leaves.

See `references/interview-guide.md` §2.1 for when a component earns a
drill-down and how to keep it consistent with the overview.

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
- Look for wrappers/adapters around internal classes and check every call
  site before drawing them — see `references/reverse-engineering.md` §8.

Full guidance on reading the analyzer's output and turning it into complete
diagrams is in `references/reverse-engineering.md`.

### 3. Write the spec

One JSON file, one entry per diagram, in the format documented exhaustively
in `references/spec-format.md` (schema, every field, worked examples, and a
table of common mistakes — read it before writing a spec for a diagram type
you haven't produced yet in this conversation). Use real names from the
conversation or the codebase, not placeholders.

#### Writing descriptions

Write each `description` as the answer to a newcomer clicking the box and
asking *"what is this for?"*. Lead with the job, in plain words, from the
point of view of the system's purpose; keep the code identity on the box's
`technology` line, where it already shows.

| ✗ Explains the wiring | ✓ Explains the job |
|---|---|
| `` `decide()` runs a DSPy `Predict` over `_GrouperSignature` with the JSONAdapter, so the reply is validated against `GrouperOutput`. The model sees the open PUs as a numbered list and answers with an index, never a pu_id. `` | Asks the AI once where the new turn belongs: does it continue one of the person's open problems, start a new one, or end one and start another? |
| `` `assemble_turns_from_docs` groups spans by `traceId`, finds AGENT root spans, pulls text from their children and resolves `user_id`. `` | Puts the pieces of each conversation back together into turns: who asked, what they asked, what the assistant answered, and when. |
| `` `_enforce_cap` keeps at most `max_open_pus_per_user` (5) open PUs and evicts the oldest by `last_ts`. `` | Keeps each person to at most five open problems by closing the oldest ones. |

Checks before you generate:
- Could someone who has never read the code understand it? Domain terms are
  fine if the diagram's `glossary` defines them.
- Does the first sentence say what the component *does* (a verb about its
  job), not which function it calls?
- Is anything left that only matters to someone editing that function
  (parameter names, internal types, call order)? Move it to `technology` or
  `key_points`, or drop it.
- For a non-obvious component, is there one sentence on why it exists (what
  would break without it)?

Docstrings copied in by `analyze_code.py` are a starting point only — they
usually describe the implementation. Rewrite them in these terms; elements
with no docstring are marked `_todo_description` in the draft spec.

### 4. Generate

```bash
python scripts/generate_diagrams.py spec.json --out-dir out/
```

Writes one self-contained, interactive `NN-<slug>-<type>-diagram.html` file per
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
location. `--draft` lets the run succeed while some descriptions are still
missing — use it only for early iterations.

### 5. Validate

The generator runs a self-check as it builds each page and every drill-down
layer: dangling edges and zero-size shapes are warnings; a `detail` whose type
differs from its parent's is a hard error (exit 1). Descriptions are checked
on every clickable box:
- **`missing description`** — fails the run (exit 1) unless `--draft` is
  passed. A set is not finished while any remain.
- **`weak description`** — a warning: the text starts with a code name, is
  mostly identifiers, walks through function calls, is over ~320 characters,
  or only repeats the label. Rewrite it to say the job (see "Writing
  descriptions"). These are heuristics: if a flagged text genuinely reads well
  to a newcomer (say, an external endpoint whose job *is* its address), leave
  it, but look at each one.

Connectors are checked on the exact lines the HTML draws (every type except
sequence, where messages crossing lifelines is the notation):
- **`connectors cross`** — two lines intersect away from their boxes.
- **`connector … runs through`** — a line passes over a box that isn't one of
  its ends (lanes, pools, groups and other containers don't count).
- **`connectors share a line`** — two unrelated lines run on top of each
  other, so nobody can tell which one continues where.
- **`label … covers` / `labels overlap`** — an edge label sits on a box or on
  another label.

These are warnings, summarised as "N misleading connector(s)". Treat each as
unfinished work: fix it in the spec (Step 6, "Fixing misleading
connectors"), and only leave one that is genuinely unavoidable and still easy
to read.

Edge routes are **baked into the output itself**. Every connector in every
type except sequence gets explicit orthogonal waypoints from the router,
which (1) never passes through an unrelated box, (2) spreads the connectors
that meet one side of a box along that side, ordered by where their other
ends are, so they fan out instead of crossing, (3) charges heavily for
crossing or riding on a connector already placed and detours where it can,
and (4) re-routes each connector once more against all the others. Edge
labels are then placed on their own line where they clear every box and
label, and the gap between ranks grows to fit the longest label. The file
you hand the user carries its routing — it doesn't depend on any viewer to
re-derive a clean path.

### 6. Look at it

Open the generated `.html` in a browser (or render it headlessly) and **click a
few boxes** to confirm the side panel shows the right name and description.
Check for: overlapping shapes, connectors crossing boxes or each other,
clipped or missing text, lopsided layout. If something looks wrong, it's almost always the spec
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

**Fixing misleading connectors.** When the router can't clear a crossing,
the diagram is asking for too much in one view; spacing won't help. In rough
order of preference:
- **Split it.** More than ~12 connectors in one view, or one box with lines
  to most others, means it should be an overview plus drill-downs (or two
  diagrams by concern: read path / write path).
- **Fold fan-in into a group.** Five modules that all depend on one library
  become one group with one line to the library, or the library goes in a
  `key_points` entry instead of being drawn.
- **Drop edges a reader doesn't need.** Transitive or obvious dependencies
  (everything uses config, everything logs) add crossings and say nothing.
- **Reorder.** Element order in the spec seeds the layout: put things that
  talk to each other next to each other; for use-case diagrams, order actors
  and use cases so each actor's use cases sit beside it.
- **Change `direction`** (`LR` ↔ `TB`) or move a node into the `lane` /
  group it actually belongs to.
- **Shorten edge labels** that cover boxes; long ones belong in the
  description or `key_points`.

Regenerate after each change and stop when the summary line is gone.

**Then read it as a newcomer, without clicking anything.** Ask:
- Are there two boxes that look interchangeable (similar names, same kind)?
- Is there an edge whose meaning isn't obvious?
- Is there a component someone would ask "why does this exist?" about?
- Do some components reach the same dependency by different routes, and is
  that difference shown and explained?

Each hit needs a *visible* fix — a stereotype, a technology line, an edge
label, a `key_points` entry — not just a description behind a click. Wrappers
and adapters are the usual culprit; see `references/reverse-engineering.md` §8.

### 6b. Coverage loop — keep going until the project is covered

For "document this project" / onboarding requests, one good pass is not the
finish line. After each round, walk the coverage checklist in
`references/interview-guide.md` §2.2 (entry points, modules, external
systems, data stores, read and write flows, decision logic, lifecycles, core
types, failure paths, config/auth, observability, deployment), and check that
every box at every level either has a drill-down or is atomic (can't be
broken into smaller components or steps). Add the missing diagrams and drill-downs, regenerate, and check
again. Stop when every row is covered or explicitly not applicable, then tell
the user what's covered, what you left out and why, and what the code
couldn't tell you.

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
