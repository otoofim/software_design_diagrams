# Diagram spec format (input to `generate_diagrams.py`)

The generator takes one JSON file describing any number of diagrams and writes
one self-contained, interactive `.html` file per diagram (plus an `index.html`
linking them). You describe *what* is in the diagram; the script decides *where*
everything goes (layered layout, containers, swimlanes, lifeline spacing) and
draws it as clickable HTML/CSS boxes over an SVG connector layer.

## Contents
1. [Top level and shared conventions](#1-top-level-and-shared-conventions)
2. [sequence](#2-sequence)
3. [flowchart](#3-flowchart)
4. [er](#4-er-entity-relationship)
5. [class](#5-class)
6. [architecture](#6-architecture)
7. [usecase](#7-usecase)
8. [activity](#8-activity)
9. [state](#9-state-state-machine)
10. [component](#10-component)
11. [deployment](#11-deployment)
12. [Common mistakes and how the generator reacts](#12-common-mistakes)

---

## 1. Top level and shared conventions

```json
{
  "title": "ShopFast",
  "diagrams": [
    {"type": "sequence", "name": "Place order", "...": "..."},
    {"type": "er", "name": "Order schema", "...": "..."}
  ]
}
```

- `type` (required): `sequence`, `flowchart`, `er` (alias `erd`), `class`,
  `architecture`, `usecase` (alias `use_case`), `activity`, `state`
  (alias `state_machine`), `component`, `deployment`.
- `name`: page title and part of the file name (`NN-<type>-<slug>.html`).
- `description` (optional, on almost any node/element — participant, flowchart
  node, ER entity, class, actor/use case, activity node, state, architecture/
  component/deployment element): free-text prose shown in the interactive HTML
  side panel when that component is clicked. Plain text; keep it to a sentence
  or two. This is additive to the structured text each type already surfaces in
  the panel (`technology`/`tech`, `stereotype`, class attributes/methods, state
  `entry`/`do`/`exit`/`activities`, ER columns). On the reverse-engineering
  path `analyze_code.py` auto-fills it from docstrings / doc-comments.
- `detail` (optional, on **any** element of **any** diagram type — an
  architecture/component/deployment element, a flowchart/activity node, an ER
  entity, a class, a state, a sequence participant, a use case, or an actor): a
  **nested diagram spec** (its own `type` + whatever keys that type uses, e.g.
  `elements`/`connections`, or `nodes`/`edges` for a flowchart) describing that
  element's internals — its process or subcomponents. The nested spec can be
  **any of the ten diagram types**, regardless of the parent's type (e.g. zoom
  from an ER entity into a `class` diagram, or from a sequence participant into
  a `flowchart`). In the interactive HTML the element becomes **zoomable**
  (marked with a ⤢): clicking it zooms in-place to render the detail diagram,
  whose own boxes stay clickable for their descriptions; a "← Overview" control
  zooms back out. The detail diagram is NOT added to the index — it lives inside
  its parent element. Drill-down is one level deep: a detail diagram's own boxes
  don't themselves carry a further zoom layer. Example:

  ```json
  {"id": "orders", "label": "Order Service", "kind": "service",
   "detail": {"type": "component", "name": "Order Service — internals",
     "elements": [
       {"id": "ctrl", "label": "OrdersController", "description": "HTTP layer."},
       {"id": "uc", "label": "CheckoutUseCase", "description": "Orchestration."}],
     "connections": [{"from": "ctrl", "to": "uc"}]}}
  ```

  It's most useful on the container-style overviews (architecture / component /
  deployment), where a box stands for a big component with real internals — but
  it works anywhere a single node is worth expanding.
- `direction`: `"TB"` (top→bottom) or `"LR"` (left→right) for every type that
  uses the layered layout (all except sequence and usecase). Defaults:
  flowchart/activity/state/class/component `TB`, er/architecture/deployment `LR`.
- `style` (on almost any node or edge): a style fragment such as
  `"fillColor=#f8cecc;strokeColor=#b85450;"` merged over the default — use it
  sparingly to highlight something (a failing path, a new service). The common
  keys are `fillColor`, `strokeColor`, `dashed=1`, and `rounded=1`.
- Ids are strings; they only need to be unique within one diagram. Keep them
  short and stable (`api`, `orders_db`), labels carry the human text.
- Keys starting with `_` (e.g. `_todo` from the analyzer) are ignored — handy
  for notes to yourself.
- Labels are plain text. The generator escapes HTML; use `\n` only where a line
  break matters (most shapes wrap automatically).

---

## 2. sequence

```json
{
  "type": "sequence", "name": "Place order", "autonumber": true,
  "participants": [
    {"id": "cust", "label": "Customer", "kind": "actor"},
    {"id": "api",  "label": "Order API"},
    {"id": "pay",  "label": "Payment Service"},
    {"id": "db",   "label": "Orders DB", "kind": "database"},
    {"id": "bus",  "label": "Event Bus", "kind": "queue"}
  ],
  "messages": [
    {"from": "cust", "to": "api", "label": "POST /orders"},
    {"from": "api",  "to": "api", "label": "validate cart"},
    {"from": "api",  "to": "db",  "label": "INSERT order"},
    {"from": "db",   "to": "api", "label": "order_id", "type": "reply"},
    {"from": "api",  "to": "pay", "label": "charge()"},
    {"from": "pay",  "to": "api", "label": "approved", "type": "reply"},
    {"from": "api",  "to": "bus", "label": "OrderPaid", "type": "async"},
    {"from": "pay",  "to": "api", "label": "declined", "type": "reply"},
    {"type": "note", "over": ["api", "pay"], "text": "Idempotency key = cart id"},
    {"from": "api",  "to": "cust", "label": "201 / 402", "type": "reply"}
  ],
  "fragments": [
    {"kind": "alt", "from": 4, "to": 7, "condition": "approved",
     "else": [{"at": 7, "condition": "declined"}]}
  ]
}
```

**participants** — order in the list = left-to-right order.
`kind`: `participant` (default box), `actor`, `boundary`, `control`, `entity`
(UML robustness icons), `database`, `queue`, `external`, `collections`
(boxes with a «stereotype» and colour).

**messages** — drawn top to bottom in list order.
- `type`: `sync` (default, filled arrow, opens an activation bar on the
  receiver), `reply` (dashed, closes the receiver's bar), `async` (open arrow,
  no bar), `create` (dashed «create», lifeline starts at that row),
  `destroy` (dashed «destroy», lifeline ends with an X), `note`.
- `from == to` draws a self-call loop (nested activation bar).
- Notes: `{"type": "note", "over": ["a"] | ["a", "b"], "text": "..."}`.
  Notes don't get a number when `autonumber` is on.
- Every sync call should normally have a matching reply later; unmatched
  bars are closed automatically at the end but the diagram reads better when
  replies are explicit.

**fragments** — combined fragments (`alt`, `opt`, `loop`, `par`, `break`,
`critical`, `ref`, any string). `from` / `to` are **0-based indexes into
`messages`** (notes count as items), inclusive. `condition` is the guard shown
next to the tab. `else` adds dashed separators: `at` is the index of the first
message of that operand. Fragments may nest; the inner one must lie fully
inside the outer range.

**Tips**: 5–25 messages per diagram reads well; split longer flows
(happy path vs failure path, or one diagram per use case). Use the real
operation names (`POST /orders`, `charge(orderId)`) in labels.

---

## 3. flowchart

```json
{
  "type": "flowchart", "name": "Checkout validation", "direction": "TB",
  "nodes": [
    {"id": "s", "kind": "start", "label": "Start"},
    {"id": "load", "kind": "process", "label": "Load cart"},
    {"id": "empty", "kind": "decision", "label": "Cart empty?"},
    {"id": "err", "kind": "io", "label": "Show error"},
    {"id": "e", "kind": "end", "label": "Done"}
  ],
  "edges": [
    {"from": "s", "to": "load"},
    {"from": "load", "to": "empty"},
    {"from": "empty", "to": "err", "label": "Yes"},
    {"from": "empty", "to": "e", "label": "No"},
    {"from": "err", "to": "e", "dashed": true}
  ]
}
```

`kind` values: `start`, `end`, `process` (default), `decision`, `io`
(parallelogram), `document`, `database`, `subprocess` (predefined process),
`manual` (manual input), `delay`, `connector` (small circle), `note`.

Edges: `label` (use `Yes`/`No` or the condition on every edge leaving a
decision), `dashed`, `style`. Loops (edges back to earlier nodes) are fine —
the layout detects cycles and routes them back up.

**Swimlanes** (cross-functional flowchart): add `"lanes": ["Customer",
"Web", "Payments"]` and give every node a `"lane"`. Lanes are drawn left to
right in list order, inside a pool titled with the diagram name.

---

## 4. er (entity-relationship)

```json
{
  "type": "er", "name": "Order schema",
  "entities": [
    {"name": "customer", "attributes": [
      {"name": "id", "type": "UUID", "pk": true},
      {"name": "email", "type": "VARCHAR(255)", "unique": true, "nullable": false}
    ]},
    {"name": "orders", "attributes": [
      {"name": "id", "type": "UUID", "pk": true},
      {"name": "customer_id", "type": "UUID", "fk": true, "nullable": false},
      "status: order_status"
    ]}
  ],
  "relationships": [
    {"from": "customer", "to": "orders", "from_card": "1", "to_card": "0..*",
     "label": "places", "from_field": "id", "to_field": "customer_id"}
  ]
}
```

**entities** — identified by `id` or `name`; optional `label` overrides the
title (the analyzer uses class name as `id` and table name as `label`).
Attributes are objects or `"name: TYPE"` shorthand strings.
- `pk`, `fk`, `unique` (or `uk`) → `PK`/`FK`/`UK` tag column; PK names underlined.
- `nullable`: defaults to `true` for non-PK columns; `false` prints `NOT NULL`.

**relationships** — `from` is conventionally the parent (the side being
referenced), `to` the child holding the FK.
- Cardinality either as `"cardinality": "1:N"` — `left:right`, where each
  side is one of the values below or `N`/`M` (= `0..*`), e.g. `1:1`, `1:N`,
  `N:1`, `N:M`, `0..1:1..*` — or per end with `from_card` / `to_card`:
  `1` (exactly one), `0..1`, `1..*`, `0..*`, `*`/`many`.
  Rendered as crow's-foot markers.
- `from_field` / `to_field` anchor the connector to specific rows (strongly
  recommended — it makes FK lines unambiguous).
- Lines are solid by default. `identifying: false` or `dashed: true` draws
  the dashed line of classic non-identifying notation (child PK does not
  include the FK) — use it consistently or not at all.
- Many-to-many: prefer drawing the junction table explicitly with two 1:N
  relationships; use `0..*`/`0..*` only for conceptual models.

---

## 5. class

```json
{
  "type": "class", "name": "Order domain",
  "classes": [
    {"name": "Entity", "stereotype": "abstract",
     "attributes": [{"name": "id", "type": "UUID", "visibility": "protected"}]},
    {"name": "Order",
     "attributes": [{"name": "status", "type": "OrderStatus", "visibility": "private"},
                    "- items: List<OrderItem>"],
     "methods": [{"name": "addItem", "params": "product: Product, qty: int"},
                 {"name": "total", "returns": "Money"},
                 {"name": "fromCart", "params": [{"name": "cart", "type": "Cart"}],
                  "returns": "Order", "static": true}]},
    {"name": "OrderStatus", "stereotype": "enum", "values": ["PENDING", "PAID"]},
    {"name": "PaymentGateway", "stereotype": "interface",
     "methods": [{"name": "charge", "params": "order: Order", "returns": "Result", "abstract": true}]}
  ],
  "relationships": [
    {"from": "Order", "to": "Entity", "kind": "inheritance"},
    {"from": "Order", "to": "OrderItem", "kind": "composition", "from_mult": "1", "to_mult": "1..*"},
    {"from": "Order", "to": "OrderStatus", "kind": "dependency"},
    {"from": "StripeGateway", "to": "PaymentGateway", "kind": "implementation"}
  ]
}
```

**classes** — identified by `id` or `name`.
- `stereotype`: `interface` (green), `abstract` (purple, italic name),
  `enum` (yellow; list constants in `values`), or any text (e.g. `entity`,
  `service`, `record`) shown as «text».
- Members: objects or preformatted strings (`"+ name: str"`,
  `"+ save(): void"`). Object fields: `name`, `type`, `visibility`
  (`public`/`private`/`protected`/`package` or `+ - # ~`), `default`,
  `static` (underlined), `abstract` (italic). Methods add `params` (string or
  list of `{name, type}` / strings) and `returns`.
- Keep ≤ ~12 members per class; hide trivial getters/setters.

**relationships** — `kind`:
| kind | meaning | arrow |
|---|---|---|
| `inheritance` / `extends` | generalization; `from` = subclass | hollow triangle |
| `implementation` / `implements` / `realization` | class → interface | dashed, hollow triangle |
| `association` | uses / has a reference | open arrow |
| `bidirectional` | association navigable both ways | plain line |
| `aggregation` | `from` = whole, shared part | hollow diamond at `from` |
| `composition` | `from` = whole, owned part | filled diamond at `from` |
| `dependency` | transient use (param, local, enum) | dashed open arrow |

`from_mult` / `to_mult` put multiplicities at the ends; `label` names the
role/association. Hierarchies are laid out with parents above children;
plain associations are "weak" and don't force vertical order.

---

## 6. architecture

```json
{
  "type": "architecture", "name": "Production",
  "elements": [
    {"id": "user", "label": "Shopper", "kind": "user"},
    {"id": "aws", "label": "AWS eu-north-1", "kind": "region", "children": [
      {"id": "vpc", "label": "VPC", "kind": "vpc", "children": [
        {"id": "alb", "label": "ALB", "kind": "load_balancer"},
        {"id": "api", "label": "order-api", "kind": "service", "technology": "FastAPI"},
        {"id": "rds", "label": "Orders DB", "kind": "database", "technology": "PostgreSQL 16"}
      ]}
    ]},
    {"id": "stripe", "label": "Stripe", "kind": "external"}
  ],
  "connections": [
    {"from": "user", "to": "alb", "protocol": "HTTPS"},
    {"from": "alb", "to": "api"},
    {"from": "api", "to": "rds", "label": "SQL"},
    {"from": "api", "to": "stripe", "protocol": "HTTPS"}
  ]
}
```

**Leaf kinds** (shape/colour): `user`/`actor` (stick figure), `client`,
`web`, `mobile`, `cdn`, `dns`, `internet` (clouds), `load_balancer`,
`api_gateway`, `firewall` (hexagons), `service` (default leaf), `function`
(serverless), `worker`, `auth`, `database`, `cache`, `search`, `queue`,
`topic` (cylinders), `storage` (folder / bucket), `external` (dashed),
`monitoring`, `container`, `generic`.

**Group kinds** (any element with `children`): `cloud`, `region`, `vpc`,
`subnet`, `cluster`, `zone`, `boundary` (dashed red trust boundary), `group`
(default), `package`, `subsystem`, `node`. Groups nest arbitrarily; connections
may target groups or anything inside them.

Element extras: `technology` (shown as `[FastAPI]`), `stereotype`, `style`.
Connection extras: `label`, `protocol` (appended as `(HTTPS)`), `kind`
(`sync` default solid arrow; `async`/`event`/`publish`/`subscribe` dashed),
`bidirectional: true`.

**Tips**: 8–25 boxes. Put managed/cloud resources inside region/VPC groups,
third parties outside. Label every connection whose protocol isn't obvious.
If a system has several big components that each have real internals worth
showing, don't force them all into one diagram — add one `architecture` (or
`component`) entry per big component, its `elements` limited to that
component's own internals, alongside an overview entry that shows the big
components as single boxes. See `interview-guide.md` §2.1.

---

## 7. usecase

```json
{
  "type": "usecase", "name": "Shop use cases", "system": "ShopFast",
  "actors": [
    {"id": "cust", "label": "Customer"},
    {"id": "admin", "label": "Store admin"},
    {"id": "psp", "label": "Payment provider", "side": "right", "kind": "system"}
  ],
  "use_cases": [
    {"id": "checkout", "label": "Checkout"},
    {"id": "pay", "label": "Pay for order"},
    {"id": "login", "label": "Log in"},
    {"id": "coupon", "label": "Apply coupon"}
  ],
  "relationships": [
    {"from": "cust", "to": "checkout"},
    {"from": "checkout", "to": "pay", "kind": "include"},
    {"from": "checkout", "to": "login", "kind": "include"},
    {"from": "coupon", "to": "checkout", "kind": "extend", "condition": "has code"},
    {"from": "psp", "to": "pay"}
  ]
}
```

- `system`: title of the boundary rectangle.
- Actor `side`: `left` (default, primary actors) or `right` (secondary/
  supporting actors). `kind: "system"` draws a «system» box instead of a
  stick figure — use it for external systems.
- Relationship `kind`: `association` (default, actor–use case line),
  `include` (base → included), `extend` (**extension → base**, optional
  `condition`), `generalization` (child → parent, actors or use cases).
- Use-case labels should be verb phrases from the actor's point of view.

---

## 8. activity

```json
{
  "type": "activity", "name": "Order fulfilment",
  "lanes": ["Customer", "Order Service", "Warehouse"],
  "nodes": [
    {"id": "i", "kind": "initial", "lane": "Customer"},
    {"id": "place", "label": "Place order", "lane": "Customer"},
    {"id": "d", "kind": "decision", "lane": "Order Service"},
    {"id": "fork", "kind": "fork", "lane": "Warehouse"},
    {"id": "pick", "label": "Pick items", "lane": "Warehouse"},
    {"id": "print", "label": "Print label", "lane": "Warehouse"},
    {"id": "join", "kind": "join", "lane": "Warehouse"},
    {"id": "cancel", "label": "Cancel order", "lane": "Order Service"},
    {"id": "f", "kind": "final", "lane": "Customer"}
  ],
  "edges": [
    {"from": "i", "to": "place"}, {"from": "place", "to": "d"},
    {"from": "d", "to": "fork", "guard": "paid"},
    {"from": "d", "to": "cancel", "guard": "declined"},
    {"from": "fork", "to": "pick"}, {"from": "fork", "to": "print"},
    {"from": "pick", "to": "join"}, {"from": "print", "to": "join"},
    {"from": "join", "to": "f"}, {"from": "cancel", "to": "f"}
  ]
}
```

Node `kind`: `action` (default, rounded box), `initial`, `final`,
`flow_final` (⊗, ends one branch only), `fork` / `join` (black bars; width
scales with the number of branches), `decision` / `merge` (small diamond;
put the question in `label` or in the incoming action), `object` (object
node, e.g. `Invoice [draft]`), `send_signal`, `accept_event`, `note`.

Edges: `guard` renders as `[guard]` (every edge leaving a decision should
have one), `label` for anything else. `lanes` (alias `partitions`) is
optional; without it the diagram is a plain vertical flow.

Activity vs flowchart: use activity when concurrency (fork/join), object
flow, responsibility partitions or UML conformance matters; flowchart for
simple business/branching logic aimed at a broad audience.

---

## 9. state (state machine)

```json
{
  "type": "state", "name": "Order lifecycle",
  "states": [
    {"id": "init", "kind": "initial"},
    {"id": "pending", "label": "Pending", "entry": "reserve stock"},
    {"id": "paid", "label": "Paid"},
    {"id": "fulfil", "label": "Fulfilment", "children": [
      {"id": "f0", "kind": "initial"},
      {"id": "picking", "label": "Picking"},
      {"id": "packed", "label": "Packed", "do": "print label"}
    ]},
    {"id": "cancelled", "label": "Cancelled", "exit": "notify"},
    {"id": "done", "kind": "final"}
  ],
  "transitions": [
    {"from": "init", "to": "pending", "event": "create"},
    {"from": "pending", "to": "paid", "event": "paymentApproved"},
    {"from": "pending", "to": "cancelled", "event": "timeout", "guard": "age > 30m", "action": "releaseStock"},
    {"from": "paid", "to": "fulfil", "event": "startFulfilment"},
    {"from": "f0", "to": "picking"},
    {"from": "picking", "to": "packed", "event": "allPicked"},
    {"from": "fulfil", "to": "done", "event": "shipped"},
    {"from": "cancelled", "to": "done"}
  ]
}
```

State `kind`: `state` (default), `composite` (automatic when `children`
exist), `initial`, `final`, `choice` (diamond), `history`, `deep_history`,
`fork`, `join`. Behaviours: `entry`, `do`, `exit` (strings) and `activities`
(list of extra `"event / action"` lines).

Transition label is built as `event [guard] / action`; all three optional.
Transitions may start/end on composite states (meaning "from any substate" /
"enter at the initial pseudostate"). Self-transitions are allowed.

Every non-final state should have at least one outgoing transition — if the
code or the user can't tell you how an object leaves a state, ask.

---

## 10. component

```json
{
  "type": "component", "name": "Order service",
  "elements": [
    {"id": "svc", "label": "Order Service", "kind": "subsystem", "children": [
      {"id": "ctrl", "label": "OrderController", "provides": ["OrdersAPI"]},
      {"id": "uc", "label": "CheckoutUseCase"},
      {"id": "repo", "label": "OrderRepository", "stereotype": "repository"}
    ]},
    {"id": "payclient", "label": "Payment Client", "requires": ["PaymentsAPI"]},
    {"id": "paysvc", "label": "Payment Service", "provides": ["PaymentsAPI"]},
    {"id": "orm", "label": "SQLAlchemy", "stereotype": "library", "technology": "2.0"}
  ],
  "connections": [
    {"from": "ctrl", "to": "uc"},
    {"from": "uc", "to": "repo"},
    {"from": "uc", "to": "payclient"},
    {"from": "repo", "to": "orm"}
  ]
}
```

- Leaves default to `kind: "component"` (box with «component» and the
  component icon). `stereotype` replaces the «component» text.
- Containers: `subsystem`, `package` (folder tab), `component` (a component
  containing components), `group`, `boundary`.
- `provides: ["Name"]` draws a lollipop interface attached to the component;
  `requires: ["Name"]` draws a dashed «use» dependency to that interface.
  Using the same interface name on both sides wires provider and consumer
  together through one shared lollipop.
- Connections default to dashed «use» dependencies; give a `label` to
  override (e.g. `"calls"`, `"publishes"`). Architecture leaf kinds
  (`database`, `queue`, …) are also accepted here.

---

## 11. deployment

```json
{
  "type": "deployment", "name": "Kubernetes deployment",
  "elements": [
    {"id": "browser", "label": "Customer browser", "kind": "device"},
    {"id": "node1", "label": "EKS worker", "kind": "node", "stereotype": "device", "technology": "m6i.large",
     "children": [
       {"id": "pod", "label": "order-api pod", "kind": "execution_environment", "technology": "Docker",
        "children": [{"id": "jar", "label": "order-api.jar", "kind": "artifact"}]}
     ]},
    {"id": "db", "label": "RDS instance", "kind": "node", "stereotype": "database server",
     "children": [{"id": "pg", "label": "PostgreSQL 16", "kind": "execution_environment"}]}
  ],
  "connections": [
    {"from": "browser", "to": "node1", "protocol": "HTTPS"},
    {"from": "pod", "to": "db", "protocol": "TCP/5432"}
  ]
}
```

- `node` (3-D box, default), `device` («device»), `execution_environment`
  («executionEnvironment» — containers, JVMs, pods, runtimes),
  `database_server`, `artifact` (note shape with «artifact»).
  Nodes/environments nest; artifacts are leaves.
- `stereotype` overrides the default «…» text, `technology` adds `[…]`.
- Connections are communication paths (plain lines). `protocol` alone is
  shown as `«HTTPS»`; `label` wins if both are given.
- Architecture leaf kinds (`load_balancer`, `database`, …) may be mixed in
  when a pure-UML look isn't required.

---

## 12. Common mistakes

| Mistake | What happens | Fix |
|---|---|---|
| Unknown id in an edge/connection/relationship | Warning printed, edge skipped | Check spelling; ids are case-sensitive |
| Unknown participant in a sequence message | Hard error (the diagram is skipped) | Add the participant |
| Duplicate ids | Hard error | Make ids unique within the diagram |
| Unknown `kind` | Warning, falls back to the default shape | Use a kind from the lists above |
| Fragment indexes off by one | Frame wraps the wrong messages | Indexes are 0-based and count notes |
| Everything in one giant diagram | Layout becomes unreadable | Split by bounded context / use case / layer |
| `extend` drawn base → extension | Arrow points the wrong way | `from` = extending use case |
| Composition `from` = part | Diamond on the wrong end | `from` = the whole |
| Expecting spacing changes to fix an edge drawn through a box | Nothing improves — that's a routing problem, not a layout one | Change what connects to what / the nesting; see SKILL.md Step 6's two failure modes |

The generator exits with status 1 if any diagram had a hard error; the
other diagrams are still written.

### Known renderer limitations

- **ER mid-edge cardinality labels can overlap a table box** on dense
  schemas. If a `1`/`N` marker lands on top of a table, nudge the layout by
  splitting the diagram or reordering entities rather than fighting the label
  placement.
- Edge routing avoids boxes it can see as obstacles, but a container that is
  an ancestor of an edge's endpoint (or a shape nested inside it) is
  deliberately *not* treated as an obstacle — a box can't block a line
  leaving itself. If an edge seems to cross such a container, that's expected;
  restructure the spec if it reads badly.
