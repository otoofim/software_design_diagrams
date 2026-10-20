# Reverse-engineering diagrams from existing code

`analyze_code.py` does **static, best-effort analysis** — regex-based for
every language except Python (which uses the real `ast` module). It needs no
build step, no dependencies installed, and no running services, which means
it can be pointed at almost any checkout immediately. That also means it
will occasionally miss something unusual or misread a heavily macro'd/
generated file. Treat its output as a very well-informed first draft, not
ground truth — always sanity-check surprising results against the actual
file it cites.

## Contents
1. [Running it](#1-running-it)
2. [Reading analysis.json](#2-reading-analysisjson)
3. [What draft-spec.json gives you, per diagram type](#3-what-draft-specjson-gives-you-per-diagram-type)
4. [What it can't do: sequence, flowchart, activity, use case](#4-what-it-cant-do)
5. [Trimming large repos](#5-trimming-large-repos)
6. [Language and framework coverage](#6-language-and-framework-coverage)
7. [When something looks wrong](#7-when-something-looks-wrong)
8. [Wrappers, adapters and layered clients](#8-wrappers-adapters-and-layered-clients)

---

## 1. Running it

```bash
python scripts/analyze_code.py /path/to/repo \
  --out analysis.json \
  --draft-spec draft-spec.json \
  --max-classes 30 \
  --depth 1
```

- `path`: any directory; it walks recursively, skipping `.git`,
  `node_modules`, `venv`/`.venv`, `dist`, `build`, `target`, and hidden dirs.
  Files over 800 KB and test files (matched by path/name heuristics like
  `tests/`, `__tests__`, `*_test.*`, `*.spec.*`) are scanned for external
  integrations only, not parsed for classes/routes.
- `--out`: full structured findings (see §2). Always write this — you'll
  want to grep it while building diagrams by hand.
- `--draft-spec`: a ready-to-generate spec covering the structural diagram
  types (§3). Optional but usually worth it as a starting point.
- `--max-classes`: caps how many classes go into the draft class diagram,
  keeping the highest-degree ones (most relationships) — real repos have
  far more classes than fit one readable diagram.
- `--depth`: how many path segments to collapse each module to in the
  draft component diagram (1 = top-level packages only).

Without `--draft-spec` it still prints a JSON summary to stdout (language
counts, class/route/model counts, samples) — useful as a quick first look
before committing to a full read.

## 2. Reading analysis.json

Top-level keys:

| key | contents |
|---|---|
| `languages` | file count per language |
| `classes` | every class/interface/enum/struct found: `name`, `file`, `line`, `language`, `module`, `kind` (`class`/`interface`/`abstract`/`enum`/`struct`), `bases`, `interfaces`, `attributes`, `methods`, `enum_values` |
| `data_models` | ORM/DDL-derived tables: `name`, `table`, `orm`, `file`, `fields` (with `pk`/`fk`/`unique`/`nullable`/`references`), `relations` |
| `routes` | `method`, `path`, `handler`, `file`, `framework` |
| `infrastructure` | `compose_services`, `k8s` (Deployments/Services/Ingresses/StatefulSets/DaemonSets/CronJobs), `terraform` (resource type+name), `dockerfiles` |
| `external_integrations` | third-party service key → files that reference it (Stripe, S3, SQS/SNS, Kafka, Redis, Mongo, OpenAI, Twilio, SendGrid, Elasticsearch, ...) |
| `modules` | every internal module/package name seen |
| `module_dependencies` | `{from, to, count}` edges between internal modules, from import statements |
| `state_candidates` | enums whose name suggests a status/state, each with `observed_transitions`: methods that assign the enum, and the values they require/forbid beforehand (from `if`/guard checks in the same method) |
| `status_assignments` | the raw per-method transition observations behind `state_candidates` |
| `manifests` | dependencies declared in `package.json`/`requirements.txt`/`go.mod`/`pom.xml`/etc. |
| `summary` | quick counts + a sample of known class names |

Grep this file for a class or table name before hand-writing a diagram that
mentions it — you'll get the exact file/line to go verify behaviour in.

## 3. What draft-spec.json gives you, per diagram type

- **class** — one diagram, the `--max-classes` highest-degree classes.
  Relationships are inferred from `bases`/`interfaces` (inheritance/
  implementation) and from attribute types that match another known class
  (association, with a `dependency` instead when the type is an enum).
  Multiplicity guesses collection-typed attributes as `*`.
- **er** — one diagram from all `data_models`. Relationships come from
  ORM-declared FKs/relationships; cardinality guesses `0..1` on the FK side
  when the column is nullable, `1` otherwise, and `to_card` is `0..*` unless
  the relation was explicitly one-to-one.
- **descriptions** — every element gets its docstring / doc-comment as its
  `description` where one exists; elements without one are marked
  `_todo_description`. Treat both as drafts: docstrings mostly explain the
  implementation, while a diagram description must say the component's job
  in plain words (see SKILL.md "Writing descriptions").
- **component** — one diagram of internal module dependencies, collapsed to
  `--depth` path segments, wrapped in a subsystem box named after the repo
  root, plus a leaf per external integration actually used by some module.
- **architecture** + **deployment** — one pair *if* a `docker-compose.yml`
  was found: services become nodes (kind guessed from image name — postgres
  → database, redis → cache, kafka/rabbitmq → queue, nginx → load_balancer,
  etc.), edges from `depends_on` and from service names spotted inside other
  services' environment variables, a `user` node wired to whichever service
  looks like the public entry point.
- **deployment** — a second, separate diagram *if* Kubernetes manifests were
  found: namespaces containing workload pods (from Deployments/StatefulSets/
  DaemonSets), Services matched to workloads by label selector, Ingress
  backends wired to the matching Service's workload.
- **state** — one diagram per status-like enum (up to 5) with any observed
  transitions. Diagrams that have essentially no inferred transitions, or
  that omitted some because the source state couldn't be determined, carry
  a `_todo` field — **always resolve every `_todo` by reading the cited
  file** before treating the diagram as finished.

Nothing is produced for a diagram type when there's no signal (e.g. no `er`
diagram if no ORM models were found).

## 4. What it can't do

Static analysis can tell you a route exists and roughly what it touches, but
not what actually happens when you call it, so these need you to read the
handler code (already narrowed down for you by `routes`/`classes`):

- **sequence diagrams** — pick the endpoint/flow that matters, open its
  handler in `routes`, follow the calls it makes (use `module_dependencies`
  and `classes[].methods` to trace which service/repository/client gets
  called), and write the message list by hand. Use the exact route path and
  method names from `analysis.json` so the diagram matches the code.
- **flowcharts** — trace the actual branches (`if`/`else`, early returns,
  validation checks) in the function bodies; the analyzer doesn't parse
  control flow beyond status-transition guards.
- **activity diagrams** — same as flowcharts but for a cross-component or
  cross-team process; look for where work fans out (async jobs, queue
  consumers, background workers) as fork/join candidates.
- **use case diagrams** — derive actors from route auth/role checks and from
  `external_integrations`/README/API docs, and use cases from route groups
  or top-level service methods; this is closer to design judgment than
  extraction.

Don't apologize for this or try to fake it from `analysis.json` alone —
just read the relevant source (the file/line is right there) before writing
these diagrams.

## 5. Trimming large repos

- Point the analyzer at a subdirectory (a single service in a monorepo)
  rather than the whole repo when the user cares about one part.
- Lower `--max-classes` and raise `--depth` for a coarser component diagram.
- Split the class/ER diagram by bounded context: filter `draft-spec.json`'s
  `classes`/`entities` by `module`/file path before generating.
- `notes` in `analysis.json` will say if the file count was capped
  (20,000-file safety limit) — mention this to the user if it fired.

## 6. Language and framework coverage

| language | classes | ORM models | routes |
|---|---|---|---|
| Python | full (`ast`) | SQLAlchemy, SQLModel, Django | Flask, FastAPI, Django urls |
| Java / Kotlin | regex | JPA/Hibernate | Spring |
| C# | regex | EF Core | ASP.NET |
| TypeScript/JavaScript | regex | TypeORM, Prisma (`.prisma` files) | Express, NestJS |
| Go | regex (structs/interfaces/methods) | — | net/http, gin-style |
| PHP, Scala | regex | — | — |
| any | — | raw SQL `CREATE TABLE` | — |

Regex-based extraction handles idiomatic, formatted code well and can miss
or misparse heavy macro use, minified/generated files (skipped for `.min.js`
and bundles), and unusual formatting. When a class you know exists doesn't
show up, check the file directly rather than assuming the model is wrong.

## 7. When something looks wrong

- **A route/class you know exists is missing** — check it isn't in a
  skipped path (test dirs, `node_modules`, minified/bundle files) and that
  the file is under the 800 KB size cap.
- **A relationship points the wrong way or is missing** — FK/relation
  direction comes from the ORM declaration; verify against the actual
  `references`/`ForeignKey` in the cited file.
- **A state diagram has almost no transitions** — the analyzer only sees
  transitions expressed as `self.status = X` (or the equivalent per
  language) inside a method body; state changes hidden behind a generic
  setter, a state-machine library, or a database trigger won't surface.
  Read the enum's usages directly.
- **Module dependency graph looks empty or wrong** — importing via a
  dependency-injection container, dynamic imports, or path aliases the
  analyzer doesn't resolve will under-count edges; this is common enough
  that it's worth spot-checking a couple of `module_dependencies` entries
  against the actual imports before trusting the component diagram fully.

## 8. Wrappers, adapters and layered clients

The most common "why do we need both?" question a newcomer asks about a
diagram is about two boxes with similar names where one wraps the other — an
HTTP client and a framework adapter around it, a repository and a caching
decorator, an SDK client and the project's own facade. Drawn as peers, they
look redundant. Drawn properly, they explain a design decision.

**Spot them.** A class that subclasses a *third-party* base (a framework's
`BaseLM`, `Session`, `Transport`, `Handler`, `Backend`, …) whose main method
mostly reshapes its arguments and calls one *internal* class is an adapter.
In `analysis.json`: an external name in `bases`, few methods, and a
`module_dependencies` edge to the module of the class it wraps.

**Check every call site.** Before drawing, grep for who uses the wrapper and
who calls the inner class directly. If some callers bypass the wrapper, that
asymmetry is usually the most important fact on the diagram — draw both
paths.

**Find the reason it exists**, in this order: the module/class docstring; the
commit or PR that introduced it (`git log --follow -- <file>`, then the PR or
issue it references); the issue tracker. Write that reason into the box's
`description` ("without it, framework X would call the API directly and skip
our tracing"). If nothing records a reason — e.g. one caller was simply never
migrated — say so ("predates X; not migrated") rather than inventing a
plausible-sounding one.

**Draw it as a wrapper**, not a peer: `stereotype: "adapter"` (or
`decorator`/`facade`), `technology` naming what it adapts, a labelled
`delegates to` edge to the inner class, and the inner class's description
stating that it is the single place that does the real work.
