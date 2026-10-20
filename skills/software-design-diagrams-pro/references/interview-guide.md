# Interview guide: what to ask before drawing

The goal of the interview is a spec that is *correct*, not a questionnaire the
user has to fill in. Ask only what you can't infer, batch questions, offer
sensible defaults the user can accept with one word, and stop asking as soon
as you can draw something useful — a draft diagram is often the best
follow-up question ("Is this right? What's missing?").

## Contents
1. [How to run the interview](#1-how-to-run-the-interview)
2. [Choosing which diagrams to produce](#2-choosing-which-diagrams-to-produce)
   - [2.1 Drill-down diagrams for big components](#21-drill-down-diagrams-for-big-components)
   - [2.2 Coverage: keep going until the whole project is covered](#22-coverage-keep-going-until-the-whole-project-is-covered)
3. [Round 1: system context (always)](#3-round-1-system-context)
4. [Per-diagram question bank](#4-per-diagram-question-bank)
5. [Consistency checks across diagrams](#5-consistency-checks-across-diagrams)
6. [Worked example](#6-worked-example)

---

## 1. How to run the interview

- **Round 1 — context** (one message, 3–6 questions): what the system is,
  who uses it, the main components/tech, scope of the documentation.
  Skip anything the user already told you.
- **Round 2 — targeted** (one message per group of diagrams, ≤ 6
  questions): only the questions from §4 whose answers you can't infer.
  Propose an answer inside the question when you can: *"I'll assume orders
  can be cancelled only before shipping — correct?"*
- **Round 3 — review**: generate, show the result, ask for corrections.
  Most refinements come from here.

Where the chat interface offers tappable choices (e.g. an
`ask_user_input`-style tool) use them for closed questions (diagram
selection, yes/no assumptions, notation choices); keep open questions in
prose. Never ask more than ~6 questions at once.

When the user says "just make something" / "use your judgement": state your
assumptions in a short list, generate, and invite corrections. Invented
details must be plausible and clearly flagged as assumptions, never passed
off as facts about their system.

If there is code, read it first (see `reverse-engineering.md`) and ask only
about what the code can't tell you: intent, runtime environment, actors,
business rules hidden in config, and which flows matter most.

---

## 2. Choosing which diagrams to produce

| The user wants to explain… | Diagram(s) |
|---|---|
| How requests travel between services/APIs over time | sequence |
| Branching business rules, validation, decision logic | flowchart |
| Database tables, keys, cardinality | er |
| Code structure: classes, inheritance, ownership | class |
| The big picture: services, infra, cloud, data stores | architecture |
| Who uses the system and for what; scope | usecase |
| A multi-step process with parallel work / responsibilities | activity |
| Lifecycle of an entity (order, ticket, payment) | state |
| Modules, libraries, interfaces between them | component |
| Where things run: servers, containers, clusters | deployment |

Typical bundles:
- **New system design doc**: usecase → architecture → component → er →
  sequence (1–3 key flows) → state (for core entities) → deployment.
- **Onboarding / "document this repo"**: architecture, component, class,
  er, sequence for the most important endpoint, deployment.
- **Feature design**: sequence + flowchart/activity for the feature, ER
  delta, state if a lifecycle changes.
- **Big/multi-component system**: overview architecture (or component)
  diagram + one drill-down diagram per big component — see §2.1.
- **Single diagram request**: produce just that one — don't pad.

If the user asked for "all" diagrams, produce all ten but keep each focused.

### 2.1 Drill-down diagrams for big components

A real system is rarely flat: it's a handful of big components (a service,
a subsystem, a bounded context) that are each, internally, their own little
architecture. Squeezing all of that into one architecture/component diagram
either hides the internals (boxes with no detail) or produces a diagram
nobody can read (40+ shapes, dense crossing connections). Neither is what a
good architect would hand someone.

Instead, produce a **set**: one overview diagram plus one drill-down per big
component.

- **Overview diagram**: the big components as single boxes (their normal
  `service`/`group`/`node` kind — no `children` needed here), wired up with
  the connections *between* them, plus the external actors/systems/data
  stores that sit outside all of them. This is the diagram that answers
  "what talks to what."
- **Drill-down diagram, one per big component**: a *separate* diagram (own
  entry in `diagrams`, own generated file) whose `elements` are that
  component's internals — its own sub-services, internal data stores,
  queues, whatever it's actually built from — wired up the same way. Add one
  element representing each *external* thing the internals talk to (the
  other big components, external systems) as a plain leaf so the diagram
  reads standalone without repeating everyone else's internals.

How to tell a component "earns" a drill-down: it has real internal
structure to show, not just a name — e.g. the user describes it as having
several services/modules of its own, code analysis (`analyze_code.py`)
finds a module dependency cluster or a whole subdirectory/service boundary
inside it, or the user explicitly says "and here's how the payment service
itself is built." A component that's genuinely a single process or a thin
wrapper doesn't need one — don't manufacture internal structure just to
produce a drill-down.

Keep the set consistent:
- Reuse the same element `id` for a component across the overview and its
  own drill-down (the drill-down's container/root id should match the id
  used for that box in the overview) — that's what lets a reader match them
  up, and it's also what `references/spec-format.md`'s cross-diagram id
  convention already recommends.
- Name the drill-down diagrams so they sort next to the overview and are
  self-explanatory in a file listing, and end every name with its diagram
  type, e.g. `"name": "Payment service — Architecture diagram"` next to
  `"name": "System overview — Architecture diagram"` (the generator slugs
  these into `01-system-overview-architecture-diagram.html`,
  `02-payment-service-architecture-diagram.html`, …; it appends the type if
  you forget).
- Mention in the overview box's label or a short note that a detail diagram
  exists for it, so someone skimming just the overview knows to look further.

This is the same idea already used for the reverse-engineering path's
"split by bounded context" advice (`SKILL.md` step 2b) — there it's driven
by the code being too big for one diagram; here it's driven by the system
being conceptually made of big parts. Both converge on the same pattern:
one map of the whole, one map per big piece.

The guidance above is written around architecture/component/deployment because
that's where drill-downs pay off most, but the inline `detail` field works on
**any element of any diagram type** — with one rule: **the drill-down is the
same type as its parent** (architecture → architecture, flowchart →
flowchart, class → class, …; see `references/spec-format.md`). A flowchart
step whose expansion is its own sub-flow is a natural inline drill-down. A
*different* view of the same thing — the request flow through an architecture
box, the classes behind an ER entity — is a separate top-level diagram; name
it in the box's `description` so the reader can find it.

**Drill-downs are recursive, all the way down.** Every box that can still be
broken into smaller components or steps gets its own `detail`, and every box
inside that detail is checked the same way. Keep zooming until each leaf is
**atomic**: it can no longer be broken down into smaller parts of the
diagram's kind. For example:

| Diagram type | A leaf is atomic when it is… |
|---|---|
| architecture / component / deployment | a single unit with no internal parts worth naming separately (one class, one function, one table, one external system, one container with one process) |
| flowchart / activity | a single action with no sub-steps or branches of its own |
| sequence | a participant whose handling of the message is one call with no further collaborators |
| state | a simple state with no sub-states |
| class / er / usecase | a class, table or use case with no meaningful parts of the same kind inside it |

External systems are atomic by definition: draw them as one box and don't
model their internals. When in doubt, go one level deeper. A drill-down with
only two boxes is fine if those really are the parts.

Don't manufacture structure: if a node really is atomic, leave it as a leaf.

### 2.2 Coverage: keep going until the whole project is covered

For a "document this project" / onboarding request, the job isn't done when
the first few diagrams look good — it's done when a newcomer could answer
every question on this checklist from the diagram set alone. Work in rounds:
draw, check the list, add diagrams or drill-downs for whatever is missing,
regenerate, check again.

| Aspect | Usually covered by |
|---|---|
| What the project does, for whom, and what it talks to | overview `architecture` + `usecase` |
| Every deployable unit / entry point (CLI, service, job, worker, UI) | `architecture` + its drill-downs |
| Every major package/module and how they depend on each other | `component` (+ drill-downs) |
| Every external system and the protocol to it | `architecture` / `component` |
| Every data store, its schema, and who reads/writes it | `er` + edges on `architecture` |
| Every main flow end-to-end, read *and* write paths | `sequence` per flow |
| Branching rules / decision logic that matter | `flowchart` / `activity` |
| Every entity with a lifecycle | `state` |
| Core domain types and abstractions (incl. adapters/wrappers) | `class` |
| Failure, retry and fallback paths | `flowchart`/`sequence` alt fragments, `key_points` |
| Configuration, auth/secrets, scheduling | `deployment` + descriptions |
| Observability (logs, traces, metrics) where it shapes the design | `architecture`/`component` |
| Where things run | `deployment` |

Every box at every level, not just in the overviews, either has a
drill-down or is atomic (see §2.1). Every row is either covered or explicitly marked not applicable.
Finish by telling the user what is covered, what you deliberately left out
and why, and what you couldn't determine from the code.

---

## 3. Round 1: system context

Ask (only what's missing):
1. What does the system do, in one or two sentences? Name?
2. Who/what interacts with it (user roles, external systems, schedulers)?
3. Main building blocks and technologies (frontend, services, DBs,
   queues, cloud)?
4. Which flows or features matter most for this documentation?
5. Audience and level of detail (executives, new engineers, auditors)?
6. Is there existing code, schema, OpenAPI, compose/k8s/terraform I can
   read? (If yes → reverse-engineering path.)

---

## 4. Per-diagram question bank

### Sequence
- Which scenario exactly (trigger → outcome)? Happy path only, or failures too?
- Participants in order of first involvement? Any async hops (queues, webhooks)?
- Synchronous vs asynchronous calls; what is returned?
- Error / timeout / retry behaviour worth showing (`alt`, `opt`, `loop`)?
- Authentication step to include or leave implicit?
- Level: service-to-service, or objects inside one service?

### Flowchart
- Start trigger and possible end states?
- Each decision: the exact question and its outcomes?
- Loops / retries and their exit conditions?
- Who performs each step (→ swimlanes)?
- Data stores or documents read/written along the way?

### ER
- Entities and their key attributes? Natural vs surrogate keys, id type?
- For each relationship: optional or mandatory on each side? one or many?
- Many-to-many links and any attributes on them (→ junction table)?
- Unique constraints, soft deletes, audit columns — show or omit?
- Physical (column types, DB-specific) or logical model?

### Class
- Which package/bounded context? Domain model only, or services too?
- Inheritance hierarchies and interfaces?
- Ownership: does the whole own its parts' lifecycle (composition) or only
  reference them (aggregation/association)? Multiplicities?
- Which members matter (hide getters/setters, framework boilerplate)?
- Enumerations and value objects?

### Architecture
- Clients/entry points (web, mobile, partners)? CDN, gateway, load balancer?
- Services and their responsibilities; sync vs async communication?
- Data stores, caches, queues/topics, object storage?
- Hosting: cloud/region/VPC/subnets/cluster boundaries? On-prem parts?
- External third parties (payments, email, identity, analytics)?
- Observability, auth, secrets management — include or keep out?

### Use case
- Primary actors (roles) and secondary actors (external systems)?
- Goals each actor achieves (verb phrases)?
- Shared sub-steps (`include`) and optional variations (`extend` + condition)?
- Actor generalization (e.g. Admin is a User)?
- System boundary name and what is explicitly out of scope?

### Activity
- Process start and end(s)? Partitions/responsible parties?
- Which steps run in parallel (fork/join) and where they re-synchronize?
- Decisions and guards; branches that terminate early (flow final)?
- Signals/events sent or awaited (emails, webhooks, timers)?
- Objects passed between steps (documents, orders in a given state)?

### State machine
- Which object/entity? All states, including initial and terminal?
- For each state: which events leave it, under which guard, with what action?
- Entry/exit/do behaviours (reserve stock, start timer)?
- Nested states (e.g. "In fulfilment" containing Picking/Packing)?
- Timeouts or automatic transitions? Can anything happen "from any state"
  (cancel, archive)?

### Component
- Components/modules and their responsibilities?
- Provided interfaces (APIs, ports) and required interfaces?
- Layering or packaging (subsystems, hexagonal ports/adapters)?
- Third-party libraries or platform services worth showing?

### Deployment
- Environments: which one to draw (prod by default)?
- Nodes: VMs, Kubernetes nodes, managed services, devices?
- Execution environments: containers, pods, JVMs, serverless runtimes?
- Artifacts deployed where (images, jars, bundles), replicas/scaling?
- Communication paths and protocols/ports between nodes?
- Network zones (public/private subnet, DMZ)?

---

## 5. Consistency checks across diagrams

Before delivering a set, make sure the diagrams agree with each other:
- Services in the sequence diagram exist in the architecture/component
  diagrams, with the same names.
- Tables in the ER diagram match entity classes in the class diagram
  (names may differ by convention; relationships must not contradict).
- States used in sequence labels/flowcharts ("status=PAID") exist in the
  state machine.
- Use cases map to at least one sequence or activity diagram when both are
  produced.
- Deployment artifacts correspond to components/services.
- Path consistency: when several components reach the same dependency, they
  either take the same route in every diagram, or the diagrams show the
  different routes and say why (or that the reason is historical/unrecorded).

Shared ids across diagrams (e.g. `api`, `orders_db`) make this easy to check.

---

## 6. Worked example

User: *"Document our ride-sharing trip booking — sequence and state diagrams."*

Round 1 (they gave little context):
> To get these right, a few quick questions:
> 1. Which services are involved in booking (e.g. mobile app, API gateway,
>    trip service, matching/dispatch, pricing, payments, notifications)?
> 2. Is driver matching synchronous or does the rider wait for a push/event?
> 3. Should the sequence include the failure path "no driver found" and
>    payment pre-authorisation failure?
> 4. Trip states I'd assume: Requested → Matching → DriverAssigned →
>    DriverArrived → InProgress → Completed, plus Cancelled and NoDriverFound.
>    Anything to add or rename? Who can cancel, and until when?

Then: write the spec (sequence with `alt` for no-driver, `async` for
matching result; state with guards `[before pickup]` on cancel), generate,
validate, preview, and ask for corrections.
