#!/usr/bin/env python3
"""
analyze_code.py - extract design facts from an existing codebase.

  python analyze_code.py PATH [--out analysis.json] [--draft-spec draft-spec.json]
                              [--max-classes 30] [--depth 1]

Finds (best effort, no build needed):
  * languages, modules/packages and the internal dependency graph between them
  * classes / interfaces / enums / structs with attributes, methods, bases
    (Python via ast; Java, Kotlin, C#, TypeScript/JavaScript, Go, PHP via regex)
  * persistence models: SQLAlchemy, SQLModel, Django, JPA/Hibernate, TypeORM,
    EF Core, Prisma schema, SQL DDL (CREATE TABLE)
  * HTTP routes: Flask, FastAPI, Django urls, Express, NestJS, Spring, ASP.NET, Go net/http/gin
  * infrastructure: docker-compose, Kubernetes manifests, Terraform resources, Dockerfiles
  * external integrations (Stripe, S3, Kafka, Redis, ...) and state-like enums

--draft-spec writes a starter spec for generate_diagrams.py (class, er, component,
architecture, deployment, state). It is a starting point: read the code to confirm
and to build sequence / flowchart / activity / use-case diagrams, which need
behavioural understanding that static extraction can't provide.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from collections import Counter, defaultdict

SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "env", "__pycache__", "dist", "build", "target",
             "bin", "obj", ".next", ".nuxt", "vendor", ".idea", ".vscode", "coverage", ".mypy_cache",
             ".pytest_cache", ".tox", "out", ".gradle", ".terraform", "site-packages", "migrations_backup"}
LANG_EXT = {".py": "python", ".java": "java", ".kt": "kotlin", ".cs": "csharp", ".ts": "typescript",
            ".tsx": "typescript", ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
            ".go": "go", ".php": "php", ".rb": "ruby", ".rs": "rust", ".scala": "scala", ".swift": "swift"}
MAX_FILE = 800_000
TEST_HINT = re.compile(r"(^|/)(tests?|__tests__|spec|specs)(/|$)|(_test|\.test|\.spec|Test|Tests)\.\w+$")

INTEGRATIONS = {
    "stripe": r"\bstripe\b", "paypal": r"\bpaypal\b", "twilio": r"\btwilio\b", "sendgrid": r"\bsendgrid\b",
    "aws-s3": r"\bboto3\b.*s3|['\"]s3['\"]|S3Client|AmazonS3|@aws-sdk/client-s3",
    "aws-sqs": r"['\"]sqs['\"]|SQSClient|AmazonSQS|@aws-sdk/client-sqs",
    "aws-sns": r"['\"]sns['\"]|SNSClient|@aws-sdk/client-sns",
    "kafka": r"\bkafka\b", "rabbitmq": r"\brabbitmq\b|\bamqp\b|\bpika\b", "redis": r"\bredis\b|\bioredis\b",
    "elasticsearch": r"\belasticsearch\b|\bopensearch\b", "postgresql": r"\bpsycopg|\bpostgres|\bpg\b|npgsql",
    "mysql": r"\bmysql\b", "mongodb": r"\bmongo(db|ose)?\b", "openai": r"\bopenai\b",
    "anthropic": r"\banthropic\b", "firebase": r"\bfirebase\b", "auth0": r"\bauth0\b",
    "keycloak": r"\bkeycloak\b", "celery": r"\bcelery\b", "graphql": r"\bgraphql\b", "grpc": r"\bgrpc\b",
    "smtp": r"\bsmtplib\b|\bnodemailer\b|JavaMailSender", "google-cloud": r"google\.cloud|@google-cloud/",
    "azure": r"\bazure\b", "sentry": r"\bsentry\b", "datadog": r"\bdatadog\b|\bddtrace\b",
}


# ============================================================ helpers
def rel(root, path):
    return os.path.relpath(path, root).replace(os.sep, "/")


def short_desc(text, cap=280):
    """First paragraph of a docstring / doc-comment, whitespace-collapsed and
    capped, for use as an element `description` in the generated diagrams."""
    if not text:
        return None
    para = re.split(r"\n\s*\n", str(text).strip(), 1)[0]
    para = re.sub(r"\s+", " ", para).strip()
    if not para:
        return None
    return para[:cap - 1].rstrip() + "…" if len(para) > cap else para


def leading_doc(src):
    """Map each declaration line (1-based) to the doc-comment immediately above
    it, for comment-based languages. Recognises `/** ... */` Javadoc/JSDoc and
    runs of `//` / `///` lines directly preceding a type/function declaration.
    Must be called on the RAW source, before strip_comments() removes comments.
    """
    lines = src.split("\n")
    decl = re.compile(r"^\s*(?:@\w+[\w.(),\s]*\s*)?"  # optional annotation line skipped separately
                      r"(?:public|private|protected|internal|export|final|abstract|static|open|"
                      r"sealed|data|func|type)?[\s]*"
                      r"(?:class|interface|struct|enum|trait|object|func|function|type)\b")
    out = {}
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        doc = None
        if "/**" in line:  # block doc-comment; find its end, then the next decl
            buf = []
            j = i
            while j < n:
                buf.append(lines[j])
                if "*/" in lines[j]:
                    break
                j += 1
            cleaned = []
            for b in buf:
                b = re.sub(r"/\*\*|\*/", "", b)
                b = re.sub(r"^\s*\*\s?", "", b)
                cleaned.append(b.strip())
            text = " ".join(x for x in cleaned if x)
            # next non-blank, non-annotation line is the declaration
            k = j + 1
            while k < n and (not lines[k].strip() or lines[k].strip().startswith("@")):
                k += 1
            if k < n and decl.search(lines[k]):
                out[k + 1] = short_desc(text)
            i = j + 1
            continue
        i += 1
    # line-comment runs: for each decl line, gather // lines directly above it
    for idx, line in enumerate(lines):
        if not decl.search(line):
            continue
        buf, k = [], idx - 1
        while k >= 0 and re.match(r"^\s*///?(?!/)", lines[k]):
            buf.append(re.sub(r"^\s*///?", "", lines[k]).strip())
            k -= 1
        if buf and (idx + 1) not in out:
            out[idx + 1] = short_desc(" ".join(reversed(buf)))
    return {ln: d for ln, d in out.items() if d}


def strip_comments(src):
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    return re.sub(r"(?<![:\"'])//.*$", "", src, flags=re.M)


def block_after(src, start):
    """Return (body, end) of the {...} block whose '{' is at/after start."""
    i = src.find("{", start)
    if i < 0:
        return "", start
    depth = 0
    j = i
    in_str = None
    while j < len(src):
        ch = src[j]
        if in_str:
            if ch == "\\":
                j += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in "\"'`":
            in_str = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[i + 1:j], j
        j += 1
    return src[i + 1:], len(src)


def top_level(body):
    """Drop everything nested deeper than the first level of braces (method bodies)."""
    out, depth = [], 0
    for ch in body:
        if ch == "{":
            depth += 1
            if depth == 1:
                out.append("{")
            continue
        if ch == "}":
            depth -= 1
            if depth == 0:
                out.append("}")
            continue
        if depth == 0:
            out.append(ch)
    return "".join(out)


def vis_from(word, name=""):
    if word in ("private", "protected", "public", "internal"):
        return "package" if word == "internal" else word
    if name.startswith("__") and not name.endswith("__"):
        return "private"
    if name.startswith("_"):
        return "protected"
    return "public"


def simple_type(t):
    """'List<Order>' -> ['List', 'Order']; used to find references to known classes."""
    return re.findall(r"[A-Za-z_][A-Za-z0-9_]*", t or "")


def is_collection(t):
    return bool(re.search(r"(List|Set|Collection|Iterable|Sequence|Array|\[\]|Many|IEnumerable|ICollection|Vec)",
                          t or ""))


# ============================================================ python
class PyAnalyzer:
    def __init__(self, root, path, module, src, out):
        self.root, self.path, self.module, self.src, self.out = root, path, module, src, out

    def run(self):
        try:
            tree = ast.parse(self.src)
        except SyntaxError:
            return
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imports.add(a.name)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parts = self.module.split(".")
                    parts = parts[:len(parts) - node.level] if len(parts) >= node.level else []
                    base = ".".join(parts + ([base] if base else []))
                imports.add(base)
                for a in node.names:
                    imports.add(f"{base}.{a.name}" if base else a.name)
        self.out["_py_imports"][self.module] = imports
        # APIRouter(prefix=...) / Blueprint(url_prefix=...) so route paths come out complete
        self.prefixes = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) \
                    and isinstance(node.targets[0], ast.Name):
                for kw in node.value.keywords:
                    if kw.arg in ("prefix", "url_prefix") and isinstance(kw.value, ast.Constant):
                        self.prefixes[node.targets[0].id] = str(kw.value.value)
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                self.klass(node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.routes(node, None)
        self.status_assignments(tree)
        if self.path.endswith("urls.py"):
            for m in re.finditer(r"path\(\s*['\"]([^'\"]*)['\"]\s*,\s*([\w.]+)", self.src):
                self.out["routes"].append({"method": "ANY", "path": "/" + m.group(1), "handler": m.group(2),
                                           "file": rel(self.root, self.path), "framework": "django"})

    def klass(self, node):
        bases = [ast.unparse(b) for b in node.bases]
        kw = {k.arg: ast.unparse(k.value) for k in node.keywords if k.arg}
        decos = [ast.unparse(d) for d in node.decorator_list]
        short_bases = [b.split(".")[-1] for b in bases]
        kind = "class"
        if any(b in ("Enum", "IntEnum", "StrEnum", "Flag") for b in short_bases):
            kind = "enum"
        elif "ABC" in short_bases or any(
                isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and
                any(ast.unparse(d).endswith("abstractmethod") for d in n.decorator_list) for n in node.body):
            kind = "abstract"
        elif "Protocol" in short_bases:
            kind = "interface"
        attrs, methods, values = [], [], []
        seen = set()
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                n = item.target.id
                if n not in seen:
                    seen.add(n)
                    attrs.append({"name": n, "type": ast.unparse(item.annotation).strip("'\""),
                                  "visibility": vis_from("", n),
                                  "_value": ast.unparse(item.value) if item.value else None})
            elif isinstance(item, ast.Assign):
                for t in item.targets:
                    if isinstance(t, ast.Name) and t.id not in seen:
                        seen.add(t.id)
                        if kind == "enum":
                            values.append(t.id)
                        else:
                            attrs.append({"name": t.id, "type": "", "visibility": vis_from("", t.id),
                                          "_value": ast.unparse(item.value)})
            elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                ds = [ast.unparse(d) for d in item.decorator_list]
                args = item.args.args
                params = []
                for a in args:
                    if a.arg in ("self", "cls"):
                        continue
                    params.append({"name": a.arg,
                                   "type": ast.unparse(a.annotation).strip("'\"") if a.annotation else ""})
                if "property" in ds:
                    if item.name not in seen:
                        seen.add(item.name)
                        attrs.append({"name": item.name,
                                      "type": ast.unparse(item.returns) if item.returns else "",
                                      "visibility": vis_from("", item.name)})
                    continue
                if item.name.startswith("__") and item.name not in ("__init__", "__call__"):
                    continue
                methods.append({"name": item.name, "params": params,
                                "returns": ast.unparse(item.returns).strip("'\"") if item.returns else "",
                                "visibility": "public" if item.name.startswith("__") else vis_from("", item.name),
                                "static": "staticmethod" in ds or "classmethod" in ds,
                                "abstract": any(d.endswith("abstractmethod") for d in ds)})
                if item.name == "__init__":
                    for st in ast.walk(item):
                        tgt = None
                        typ = ""
                        if isinstance(st, ast.Assign):
                            tgt = st.targets[0]
                        elif isinstance(st, ast.AnnAssign):
                            tgt, typ = st.target, ast.unparse(st.annotation)
                        if isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Name) \
                                and tgt.value.id == "self" and tgt.attr not in seen:
                            seen.add(tgt.attr)
                            if not typ:
                                match = next((p["type"] for p in params
                                              if isinstance(st.value, ast.Name) and p["name"] == st.value.id), "")
                                typ = match
                            attrs.append({"name": tgt.attr, "type": typ, "visibility": vis_from("", tgt.attr)})
                self.routes(item, node.name)
        cls = {"name": node.name, "file": rel(self.root, self.path), "line": node.lineno, "language": "python",
               "module": self.module, "kind": kind, "bases": [b for b in bases if b not in ("object",)],
               "interfaces": [], "decorators": decos, "attributes": attrs, "methods": methods,
               "enum_values": values, "description": short_desc(ast.get_docstring(node))}
        self.out["classes"].append(cls)
        self.model(cls, short_bases, kw, node)
        cls["attributes"] = [a for a in attrs if not a["name"].startswith("__")]

    def model(self, cls, short_bases, kw, node):
        attrs = {a["name"]: a for a in cls["attributes"]}
        tablename = attrs.get("__tablename__", {}).get("_value")
        is_django = any(b in ("Model",) for b in short_bases) and any("models.Model" in b for b in cls["bases"])
        is_sqlmodel = "SQLModel" in short_bases and kw.get("table") == "True"
        is_sqla = bool(tablename) or any(b in ("Base", "DeclarativeBase", "Model") for b in short_bases) and \
            any(re.search(r"(mapped_column|Column)\(", a.get("_value") or "") for a in attrs.values())
        if not (is_django or is_sqlmodel or is_sqla):
            return
        orm = "django" if is_django else "sqlmodel" if is_sqlmodel else "sqlalchemy"
        table = (tablename or "").strip("'\"") or (cls["name"].lower() if orm != "django"
                                                     else f"{cls['name'].lower()}")
        fields, relations = [], []
        if orm == "django":
            fields.append({"name": "id", "type": "AutoField", "pk": True})
        for a in cls["attributes"]:
            v = a.get("_value") or ""
            n = a["name"]
            if n.startswith("__") or n == "Meta" or n == "objects":
                continue
            call = re.match(r"(?:[\w.]+\.)?(\w+)\((.*)\)$", v, re.S)
            if orm == "django":
                if not call or not call.group(1).endswith("Field") and call.group(1) not in ("ForeignKey",):
                    continue
                ftype, args = call.group(1), call.group(2)
                if ftype in ("ForeignKey", "OneToOneField", "ManyToManyField"):
                    target = re.match(r"\s*['\"]?([\w.]+)['\"]?", args).group(1).split(".")[-1]
                    if ftype == "ManyToManyField":
                        relations.append({"field": n, "target": target, "kind": "many_to_many"})
                        continue
                    fields.append({"name": f"{n}_id", "type": "FK", "fk": True, "references": target,
                                   "nullable": "null=True" in args, "unique": ftype == "OneToOneField"})
                    relations.append({"field": f"{n}_id", "target": target,
                                      "kind": "one_to_one" if ftype == "OneToOneField" else "many_to_one"})
                    continue
                fields.append({"name": n, "type": ftype.replace("Field", ""),
                               "pk": "primary_key=True" in args, "nullable": "null=True" in args,
                               "unique": "unique=True" in args})
                continue
            # SQLAlchemy / SQLModel
            if re.search(r"relationship\(", v):
                m = re.search(r"relationship\(\s*['\"]?(\w+)", v)
                typ = a.get("type", "")
                target = m.group(1) if m and m.group(1) not in ("back_populates",) else \
                    (simple_type(typ)[-1] if simple_type(typ) else "?")
                relations.append({"field": n, "target": target,
                                  "kind": "one_to_many" if is_collection(typ) or "uselist" in v else "relation"})
                if not typ:
                    self.out["_sqla_rels"].append((a, target, table, "uselist=False" in v.replace(" ", "")))
                continue
            col = re.search(r"(mapped_column|Column|Field)\((.*)\)", v, re.S)
            if not col and not (orm == "sqlmodel" and a.get("type")):
                if orm == "sqlalchemy" and a.get("type", "").startswith("Mapped["):
                    col = None
                else:
                    continue
            args = col.group(2) if col else ""
            typ = a.get("type", "")
            typ = re.sub(r"^Mapped\[(.*)\]$", r"\1", typ)
            first = re.match(r"\s*([A-Z]\w*(?:\([^)]*\))?)", args)
            if first and not first.group(1).startswith("ForeignKey"):
                typ = first.group(1)
            fk = re.search(r"(?:ForeignKey\(|foreign_key=)\s*['\"]([\w.]+)['\"]", args)
            flat_args = re.sub(r"\s+", "", args)
            pk = "primary_key=True" in flat_args
            raw_t = a.get("type") or ""
            if pk or "nullable=False" in flat_args:
                nullable = False
            elif "nullable=True" in flat_args:
                nullable = True
            elif raw_t:  # typed (Mapped[...] / SQLModel): nullability follows Optional
                nullable = "Optional" in raw_t or "None" in raw_t
            else:        # classic Column(): nullable unless told otherwise
                nullable = True
            f = {"name": n, "type": typ, "pk": pk, "nullable": nullable,
                 "unique": "unique=True" in flat_args}
            if fk:
                f["fk"] = True
                f["references"] = fk.group(1)
                relations.append({"field": n, "target": fk.group(1).split(".")[0], "kind": "many_to_one"})
            if not a.get("type") and typ:
                a["type"] = re.sub(r"^Enum\((\w+)\)$", r"\1", typ)
            fields.append(f)
        self.out["data_models"].append({"name": cls["name"], "table": table, "orm": orm, "file": cls["file"],
                                        "fields": fields, "relations": relations,
                                        "description": cls.get("description")})

    def routes(self, fn, owner):
        for d in fn.decorator_list:
            txt = ast.unparse(d)
            m = re.match(r"([\w.]+)\.(get|post|put|patch|delete|route|api_route|websocket)\((.*)\)$", txt, re.S)
            if not m:
                continue
            method = m.group(2).upper()
            pm = re.match(r"\s*['\"]([^'\"]*)['\"]", m.group(3))
            path = pm.group(1) if pm else "?"
            prefix = self.prefixes.get(m.group(1).split(".")[-1], "")
            if prefix:
                path = "/" + "/".join(x.strip("/") for x in (prefix, path) if x.strip("/"))
            if method in ("ROUTE", "API_ROUTE"):
                mm = re.search(r"methods\s*=\s*\[([^\]]*)\]", m.group(3))
                method = ",".join(x.strip(" '\"").upper() for x in mm.group(1).split(",")) if mm else "GET"
            self.out["routes"].append({"method": method, "path": path,
                                       "handler": f"{owner}.{fn.name}" if owner else fn.name,
                                       "file": rel(self.root, self.path), "line": fn.lineno,
                                       "framework": "fastapi/flask"})

    def status_assignments(self, tree):
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            sets, checks = set(), set()
            invert = {"Eq": "NotEq", "NotEq": "Eq", "In": "NotIn", "NotIn": "In"}
            guard_tests = set()
            for st in ast.walk(fn):
                # "if status != X: raise/return" is a guard clause -> it *requires* X
                if isinstance(st, ast.If) and st.body and all(isinstance(b, (ast.Raise, ast.Return))
                                                              for b in st.body):
                    guard_tests.add(id(st.test))
            for st in ast.walk(fn):
                if isinstance(st, ast.Assign) and isinstance(st.targets[0], ast.Attribute) and \
                        re.search(r"(status|state)$", st.targets[0].attr, re.I):
                    sets.add(ast.unparse(st.value))
                if isinstance(st, ast.Compare) and isinstance(st.left, ast.Attribute) and \
                        re.search(r"(status|state)$", st.left.attr, re.I):
                    op = type(st.ops[0]).__name__
                    if id(st) in guard_tests:
                        op = invert.get(op, op)
                    for c in st.comparators:
                        vals = c.elts if isinstance(c, (ast.Tuple, ast.List, ast.Set)) else [c]
                        for v in vals:
                            checks.add((op, ast.unparse(v)))
            if sets:
                self.out["_transitions"].append({"function": fn.name, "file": rel(self.root, self.path),
                                                 "line": fn.lineno, "sets": sorted(sets),
                                                 "requires": sorted(c for op, c in checks if op in ("Eq", "In")),
                                                 "forbids": sorted(c for op, c in checks if op in ("NotEq", "NotIn"))})


# ============================================================ C-like languages
CLASS_RE = re.compile(
    r"(?P<annos>(?:@\w+(?:\([^)]*\))?\s*|\[[\w.]+(?:\([^)]*\))?\]\s*)*)"
    r"(?P<mods>(?:(?:public|private|protected|internal|abstract|final|sealed|static|export|default|"
    r"data|open|partial|readonly)\s+)*)"
    r"(?P<kind>class|interface|enum|record|struct|trait)\s+(?P<name>[A-Z]\w*)"
    r"(?P<generic>\s*<[^{]*?>)?(?P<ctor>\s*\([^)]*\))?(?P<rest>[^{;]*)\{", re.M)


def parse_heritage(rest, lang):
    bases, ifaces = [], []
    rest = rest.strip()
    m = re.search(r"\bextends\s+([\w.<>, ]+?)(?=\s+implements\b|$)", rest)
    if m:
        items = [x.strip() for x in re.split(r",(?![^<]*>)", m.group(1)) if x.strip()]
        bases += items
    m = re.search(r"\bimplements\s+([\w.<>, ]+)", rest)
    if m:
        ifaces += [x.strip() for x in re.split(r",(?![^<]*>)", m.group(1)) if x.strip()]
    if rest.startswith(":") and lang in ("csharp", "kotlin", "swift"):
        items = [re.sub(r"\(.*\)", "", x).strip() for x in re.split(r",(?![^<]*>)", rest[1:]) if x.strip()]
        items = [re.sub(r"\s*where\b.*", "", x) for x in items]
        for x in items:
            if lang == "csharp" and re.match(r"I[A-Z]", x):
                ifaces.append(x)
            else:
                bases.append(x)
    return [b.split("<")[0] for b in bases], [i.split("<")[0] for i in ifaces]


FIELD_JAVA = re.compile(
    r"(?P<annos>(?:@\w+(?:\((?:[^()]|\([^)]*\))*\))?\s*)*)(?P<mods>(?:(?:public|private|protected|static|final|"
    r"transient|volatile|readonly|internal|const|required)\s+)*)(?P<type>[\w.]+(?:<[^;=(){}]*>)?(?:\[\])*\??)\s+"
    r"(?P<name>\w+)\s*(?:=[^;]*)?;")
PROP_CS = re.compile(
    r"(?P<annos>(?:\[[^\]]+\]\s*)*)(?P<mods>(?:(?:public|private|protected|internal|static|virtual|override|"
    r"required|abstract)\s+)*)(?P<type>[\w.]+(?:<[^;=(){}]*>)?(?:\[\])*\??)\s+(?P<name>\w+)\s*\{\s*(?:get|set|init)")
METHOD_C = re.compile(
    r"(?P<annos>(?:@\w+(?:\([^)]*\))?\s*|\[[^\]]+\]\s*)*)(?P<mods>(?:(?:public|private|protected|internal|static|"
    r"final|abstract|virtual|override|async|synchronized|default|open|suspend|fun)\s+)*)"
    r"(?:(?P<type>[\w.]+(?:<[^;(){}]*>)?(?:\[\])*\??)\s+)?(?P<name>\w+)\s*\((?P<params>[^)]*)\)"
    r"(?:\s*:\s*(?P<ret>[\w.<>\[\]?, ]+?))?\s*(?:throws\s+[\w., ]+)?\s*(?P<end>\{|;|=>)")
FIELD_TS = re.compile(
    r"(?P<annos>(?:@\w+\((?:[^()]|\([^)]*\))*\)\s*)*)(?P<mods>(?:(?:public|private|protected|static|readonly|"
    r"declare|abstract)\s+)*)(?P<name>#?\w+)[?!]?\s*:\s*(?P<type>[^;=\n]+?)\s*(?:=[^;\n]*)?;")
KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "new", "else", "try", "do", "using", "lock",
            "foreach", "synchronized", "super", "this", "throw", "typeof", "function", "constructor_"}


def analyze_c_like(root, path, lang, src, out):
    docs = leading_doc(src)  # from RAW src, before comments are stripped
    clean = strip_comments(src)
    pkg = None
    m = re.search(r"^\s*package\s+([\w.]+)", clean, re.M)
    if m and lang in ("java", "kotlin", "scala"):
        pkg = m.group(1)
    m = re.search(r"^\s*namespace\s+([\w.\\]+)", clean, re.M)
    if m and lang in ("csharp", "php"):
        pkg = m.group(1).replace("\\", ".")
    out["_pkg_of_file"][rel(root, path)] = pkg
    for m in CLASS_RE.finditer(clean):
        name = m.group("name")
        kind = m.group("kind")
        mods = m.group("mods") or ""
        annos = m.group("annos") or ""
        body, _end = block_after(clean, m.end() - 1)
        tl = top_level(body)
        bases, ifaces = parse_heritage(m.group("rest") or "", lang)
        if kind == "trait":
            kind = "interface"
        if "abstract" in mods and kind == "class":
            kind = "abstract"
        attrs, methods, values = [], [], []
        if kind == "enum":
            first = tl.split(";")[0]
            values = [v.strip().split("(")[0].split("=")[0].strip() for v in first.split(",")]
            values = [v for v in values if re.match(r"^[A-Za-z_]\w*$", v)]
        # constructor-declared properties (Kotlin data class / TS parameter properties / records)
        if m.group("ctor"):
            for part in m.group("ctor").strip()[1:-1].split(","):
                pm = re.match(r"\s*(?:@\w+\s*)*(?:(private|protected|public)\s+)?(?:val|var|readonly)?\s*"
                              r"(\w+)\s*:\s*([\w<>?, .\[\]]+)", part)
                if pm:
                    attrs.append({"name": pm.group(2), "type": pm.group(3).strip(),
                                  "visibility": vis_from(pm.group(1) or "public")})
                pm2 = re.match(r"\s*([\w<>\[\]?.]+)\s+(\w+)\s*$", part)
                if not pm and pm2 and lang in ("java", "csharp"):
                    attrs.append({"name": pm2.group(2), "type": pm2.group(1), "visibility": "public"})
        field_res = []
        if lang in ("java", "csharp", "php", "scala"):
            field_res.append(FIELD_JAVA)
        if lang == "csharp":
            field_res.append(PROP_CS)
        if lang in ("typescript", "javascript"):
            field_res.append(FIELD_TS)
        if lang == "kotlin":
            field_res.append(re.compile(r"(?P<annos>(?:@\w+(?:\([^)]*\))?\s*)*)(?P<mods>(?:(?:private|protected|"
                                        r"public|internal|lateinit|override|open)\s+)*)(?:val|var)\s+(?P<name>\w+)"
                                        r"\s*:\s*(?P<type>[\w<>?, .]+)"))
        seen = {a["name"] for a in attrs}
        for fre in field_res:
            for fm in fre.finditer(tl):
                n = fm.group("name").lstrip("#")
                t = fm.group("type").strip()
                if n in seen or t in ("return", "new", "throw", "else") or n in KEYWORDS:
                    continue
                seen.add(n)
                mods_f = fm.group("mods") or ""
                vis = next((w for w in ("private", "protected", "public", "internal") if w in mods_f), None)
                if vis is None:
                    vis = "private" if fm.group("name").startswith("#") else (
                        "package" if lang == "java" else "public" if lang != "csharp" else "private")
                attrs.append({"name": n, "type": t, "visibility": vis_from(vis) if vis != "package" else "package",
                              "static": "static" in mods_f, "_annos": fm.group("annos") or ""})
        for mm in METHOD_C.finditer(tl):
            n = mm.group("name")
            if n in KEYWORDS or n[0].isupper() and n != name and lang != "csharp" and lang != "go":
                continue
            typ = (mm.group("type") or "").strip()
            if typ in ("return", "new", "else", "throw", "await", "yield"):
                continue
            mods_m = mm.group("mods") or ""
            if lang in ("typescript", "javascript") and not typ and mm.group("end") != "{" and kind != "interface":
                continue
            if lang in ("java", "csharp") and not typ and n != name:
                continue
            if n == name or n == "constructor":
                n = name if lang != "typescript" else "constructor"
                typ = ""
            params = []
            for p in re.split(r",(?![^<]*>)", mm.group("params")):
                p = re.sub(r"@\w+(\([^)]*\))?\s*", "", p).strip()
                if not p:
                    continue
                if ":" in p:
                    pn, pt = p.split(":", 1)
                    pn = re.sub(r"^(private|public|protected|readonly)\s+", "", pn.strip())
                    params.append({"name": pn.strip(" ?"), "type": pt.split("=")[0].strip()})
                else:
                    bits = p.split("=")[0].split()
                    params.append({"name": bits[-1], "type": " ".join(bits[:-1])} if len(bits) > 1
                                  else {"name": bits[0], "type": ""})
            vis = next((w for w in ("private", "protected", "public", "internal") if w in mods_m), "public")
            if n.startswith("#"):
                vis = "private"
            methods.append({"name": n, "params": params, "returns": (mm.group("ret") or typ or "").strip(),
                            "visibility": vis_from(vis), "static": "static" in mods_m,
                            "abstract": "abstract" in mods_m or (kind == "interface"),
                            "_annos": mm.group("annos") or ""})
        line_no = clean[:m.start()].count("\n") + 1
        cls = {"name": name, "file": rel(root, path), "line": line_no,
               "language": lang, "module": pkg or os.path.dirname(rel(root, path)), "kind": kind,
               "bases": bases, "interfaces": ifaces, "decorators": re.findall(r"[@\[](\w+)", annos),
               "attributes": attrs, "methods": methods, "enum_values": values,
               "description": docs.get(line_no)}
        out["classes"].append(cls)
        jpa_typeorm_ef(cls, annos, out)
        class_routes(cls, annos, body, path, root, lang, out)
    if lang == "go":
        analyze_go(root, path, clean, out, docs)
    c_like_transitions(root, path, clean, out)
    # free-standing routes (Express / Go / minimal APIs)
    for rm in re.finditer(r"\b(app|router|server|api|r|e|g|mux)\s*\.\s*(get|post|put|patch|delete|"
                          r"Get|Post|Put|Patch|Delete|GET|POST|PUT|PATCH|DELETE|HandleFunc|Handle|"
                          r"MapGet|MapPost|MapPut|MapDelete)\s*\(\s*[\"'`]([^\"'`]+)[\"'`]\s*,\s*([\w.]+)?", clean):
        method = rm.group(2).upper().replace("MAP", "")
        if method in ("HANDLEFUNC", "HANDLE"):
            method = "ANY"
        handler = rm.group(4)
        if not handler or handler in ("async", "function", "req", "request", "ctx", "c", "w"):
            handler = "(inline)"
        out["routes"].append({"method": method, "path": rm.group(3), "handler": handler,
                              "file": rel(root, path), "line": clean[:rm.start()].count("\n") + 1,
                              "framework": "express/go/minimal"})
    # imports
    imps = set()
    for im in re.finditer(r"""(?:import\s+(?:[\w{}\s,*]+\s+from\s+)?|require\(\s*|export\s+[\w{}\s,*]+\s+from\s+)"""
                          r"""['"]([^'"]+)['"]""", clean):
        imps.add(im.group(1))
    for im in re.finditer(r"^\s*import\s+(?:static\s+)?([\w.]+)", clean, re.M):
        if lang in ("java", "kotlin", "scala"):
            imps.add(im.group(1))
    for im in re.finditer(r"^\s*using\s+(?:static\s+)?([\w.]+)\s*;", clean, re.M):
        imps.add(im.group(1))
    for im in re.finditer(r"^\s*use\s+([\w\\]+)", clean, re.M):
        if lang == "php":
            imps.add(im.group(1).replace("\\", "."))
    gm = re.search(r"import\s*\(([^)]*)\)", clean)
    if lang == "go":
        for im in re.finditer(r"\"([^\"]+)\"", gm.group(1) if gm else ""):
            imps.add(im.group(1))
        for im in re.finditer(r"^import\s+\"([^\"]+)\"", clean, re.M):
            imps.add(im.group(1))
    out["_c_imports"][rel(root, path)] = imps


METHOD_HEAD = re.compile(r"(?:\b(?:public|private|protected|internal|fun|func|async|override|static|final)\s+)+"
                         r"[\w<>\[\]?, .*]*?\b(\w+)\s*\([^()]*\)[^{;=]*\{")
STATUS_SET = re.compile(r"(?:\b(?:this|self)\s*\.\s*|\$this->|\b\w\s*\.\s*)?(\w*(?:[Ss]tatus|[Ss]tate))\s*=(?!=)\s*"
                        r"([A-Z]\w*)\s*[.:]+\s*([A-Z][A-Z0-9_]*)\b")
STATUS_SETTER = re.compile(r"\bset(?:Status|State)\s*\(\s*([A-Z]\w*)\.([A-Z][A-Z0-9_]*)\s*\)")
STATUS_CMP = re.compile(r"\b\w*(?:[Ss]tatus|[Ss]tate)(?:\(\))?\s*(==|!=|===|!==)\s*([A-Z]\w*)\s*[.:]+\s*"
                        r"([A-Z][A-Z0-9_]*)\b")


def c_like_transitions(root, path, clean, out):
    """Find methods that assign a status/state enum value and the checks guarding them."""
    for m in METHOD_HEAD.finditer(clean):
        name = m.group(1)
        if name in KEYWORDS:
            continue
        body, _ = block_after(clean, m.end() - 1)
        sets = {f"{g2}.{g3}" for _, g2, g3 in STATUS_SET.findall(body)}
        sets |= {f"{a}.{b}" for a, b in STATUS_SETTER.findall(body)}
        if not sets:
            continue
        requires, forbids = set(), set()
        for cm in STATUS_CMP.finditer(body):
            val = f"{cm.group(2)}.{cm.group(3)}"
            positive = cm.group(1) in ("==", "===")
            # "if (status != X) throw ..." is a guard clause: X is required
            after = body[cm.end():cm.end() + 80]
            if re.match(r"[^{;]*\)\s*\{?\s*(throw|return)\b", after):
                positive = not positive
            (requires if positive else forbids).add(val)
        out["_transitions"].append({"function": name, "file": rel(root, path),
                                    "line": clean[:m.start()].count("\n") + 1, "sets": sorted(sets),
                                    "requires": sorted(requires), "forbids": sorted(forbids)})


def jpa_typeorm_ef(cls, annos, out):
    decos = cls["decorators"]
    lang = cls["language"]
    if "Entity" not in decos and "Table" not in decos and not (lang == "csharp" and cls["name"] in out["_ef_sets"]):
        return
    if lang == "csharp" and "Entity" not in decos and cls["name"] not in out["_ef_sets"]:
        return
    tm = re.search(r"@(?:Table|Entity)\(\s*(?:name\s*=\s*)?[\"']([\w.]+)[\"']", annos) or \
        re.search(r"\[Table\(\s*\"([\w.]+)\"", annos)
    table = tm.group(1) if tm else cls["name"].lower()
    fields, relations = [], []
    for a in cls["attributes"]:
        an = a.get("_annos", "")
        typ = a.get("type", "")
        name = a["name"]
        rel_m = re.search(r"@(ManyToOne|OneToOne|OneToMany|ManyToMany)\b(?:\(\s*\(\)\s*=>\s*(\w+))?", an)
        if rel_m:
            target = rel_m.group(2) or [t for t in simple_type(typ)
                                        if t not in ("List", "Set", "Collection", "Array", "Promise")][-1]
            kind = {"ManyToOne": "many_to_one", "OneToOne": "one_to_one", "OneToMany": "one_to_many",
                    "ManyToMany": "many_to_many"}[rel_m.group(1)]
            if kind in ("many_to_one", "one_to_one"):
                jc = re.search(r"@JoinColumn\(\s*(?:name\s*[=:]\s*)?[\"'](\w+)[\"']", an)
                col = jc.group(1) if jc else f"{name}_id"
                fields.append({"name": col, "type": "FK", "fk": True, "references": target,
                               "nullable": not re.search(r"nullable[=:]false|optional[=:]false|@NotNull",
                                                         re.sub(r"\s+", "", an)),
                               "unique": kind == "one_to_one"})
                relations.append({"field": col, "target": target, "kind": kind})
            else:
                relations.append({"field": name, "target": target, "kind": kind})
            continue
        if lang == "csharp":
            if typ.split("<")[0] in ("ICollection", "List", "IEnumerable", "HashSet"):
                relations.append({"field": name, "target": simple_type(typ)[-1], "kind": "one_to_many"})
                continue
            if typ in out["_ef_sets"]:
                continue  # navigation property; the FK is the XxxId property
        if "Transient" in an or "NotMapped" in an:
            continue
        pk = bool(re.search(r"@(Id|PrimaryGeneratedColumn|PrimaryColumn)\b|\[Key\]", an)) or \
            (lang == "csharp" and name in ("Id", f"{cls['name']}Id"))
        flat = re.sub(r"\s+", "", an)
        if pk or re.search(r"nullable[=:]false|optional=false|@NotNull|@NonNull|@NotBlank|\[Required\]", flat):
            nullable = False
        elif re.search(r"nullable[=:]true", flat) or typ.endswith("?"):
            nullable = True
        elif lang in ("java", "kotlin"):
            # JPA columns are nullable by default unless the Java type is a primitive
            nullable = typ not in ("int", "long", "short", "byte", "boolean", "double", "float", "char") \
                and not (lang == "kotlin" and not typ.endswith("?"))
        else:
            nullable = False  # TypeORM defaults to NOT NULL; EF uses '?' (handled above)
        f = {"name": name, "type": re.sub(r"\?$", "", typ), "pk": pk, "nullable": nullable,
             "unique": "unique=true" in flat or "unique:true" in flat}
        if lang == "csharp" and name.endswith("Id") and not pk and name[:-2] in out["_ef_sets"]:
            f["fk"] = True
            f["references"] = name[:-2]
            relations.append({"field": name, "target": name[:-2], "kind": "many_to_one"})
        fields.append(f)
    out["data_models"].append({"name": cls["name"], "table": table,
                               "orm": {"java": "jpa", "kotlin": "jpa", "typescript": "typeorm",
                                       "csharp": "efcore"}.get(lang, lang),
                               "file": cls["file"], "fields": fields, "relations": relations})


def class_routes(cls, annos, body, path, root, lang, out):
    base = ""
    bm = re.search(r"@(?:RequestMapping|Controller|Path)\(\s*(?:value\s*=\s*|path\s*=\s*)?[\"']([^\"']*)", annos) \
        or re.search(r"\[Route\(\"([^\"]*)\"", annos)
    if bm:
        base = bm.group(1).replace("[controller]", cls["name"].replace("Controller", "").lower())
    for mm in re.finditer(r"@(Get|Post|Put|Patch|Delete|Request)(?:Mapping)?(?:\(\s*(?:value\s*=\s*|path\s*=\s*)?"
                          r"(?:[\"']([^\"']*)[\"'])?[^)]*\))?\s+(?:@\w+(?:\([^)]*\))?\s*)*"
                          r"(?:public|private|protected|async|\s)*[\w<>\[\]?,. ]*?\s*(\w+)\s*\(", body):
        verb = mm.group(1).upper()
        out["routes"].append({"method": "ANY" if verb == "REQUEST" else verb,
                              "path": "/" + "/".join(x.strip("/") for x in (base, mm.group(2) or "") if x.strip("/")),
                              "handler": f"{cls['name']}.{mm.group(3)}", "file": rel(root, path),
                              "framework": "spring/nestjs/jaxrs"})
    for mm in re.finditer(r"\[Http(Get|Post|Put|Patch|Delete)(?:\(\"([^\"]*)\"\))?\][^{;]*?\s(\w+)\s*\(", body):
        out["routes"].append({"method": mm.group(1).upper(),
                              "path": "/" + "/".join(x.strip("/") for x in (base, mm.group(2) or "") if x.strip("/")),
                              "handler": f"{cls['name']}.{mm.group(3)}", "file": rel(root, path),
                              "framework": "aspnet"})


def analyze_go(root, path, clean, out, docs=None):
    docs = docs or {}
    for m in re.finditer(r"^type\s+([A-Z]\w*)\s+(struct|interface)\s*\{", clean, re.M):
        body, _ = block_after(clean, m.end() - 1)
        attrs, methods, embeds = [], [], []
        for line in body.splitlines():
            line = re.sub(r"`.*`", "", line).strip()
            if not line:
                continue
            if m.group(2) == "interface":
                mm = re.match(r"(\w+)\((.*?)\)\s*(.*)", line)
                if mm:
                    methods.append({"name": mm.group(1), "params": mm.group(2), "returns": mm.group(3),
                                    "visibility": "public" if mm.group(1)[0].isupper() else "private",
                                    "abstract": True})
                continue
            parts = line.split()
            if len(parts) == 1:
                embeds.append(parts[0].lstrip("*"))
            elif len(parts) >= 2:
                for n in parts[0].split(","):
                    n = n.strip()
                    if n:
                        attrs.append({"name": n, "type": " ".join(parts[1:]),
                                      "visibility": "public" if n[0].isupper() else "private"})
        go_line = clean[:m.start()].count("\n") + 1
        out["classes"].append({"name": m.group(1), "file": rel(root, path),
                               "line": go_line, "language": "go",
                               "module": os.path.dirname(rel(root, path)),
                               "kind": "interface" if m.group(2) == "interface" else "struct",
                               "bases": embeds, "interfaces": [], "decorators": [], "attributes": attrs,
                               "methods": methods, "enum_values": [], "description": docs.get(go_line)})
    for m in re.finditer(r"^func\s+\(\s*\w+\s+\*?(\w+)\s*\)\s+(\w+)\s*\(([^)]*)\)\s*([^{]*)\{", clean, re.M):
        for c in out["classes"]:
            if c["name"] == m.group(1) and c["language"] == "go":
                c["methods"].append({"name": m.group(2), "params": m.group(3).strip(),
                                     "returns": m.group(4).strip(),
                                     "visibility": "public" if m.group(2)[0].isupper() else "private"})
                break


# ============================================================ schema files
def analyze_prisma(root, path, src, out):
    enums = {m.group(1) for m in re.finditer(r"^enum\s+(\w+)", src, re.M)}
    models = {m.group(1) for m in re.finditer(r"^model\s+(\w+)", src, re.M)}
    for m in re.finditer(r"^enum\s+(\w+)\s*\{([^}]*)\}", src, re.M):
        vals = [v.strip() for v in m.group(2).split() if re.match(r"^\w+$", v.strip())]
        out["classes"].append({"name": m.group(1), "file": rel(root, path), "language": "prisma",
                               "module": "prisma", "kind": "enum", "bases": [], "interfaces": [],
                               "decorators": [], "attributes": [], "methods": [], "enum_values": vals})
    for m in re.finditer(r"^model\s+(\w+)\s*\{([^}]*)\}", src, re.M):
        fields, relations = [], []
        table = m.group(1)
        tm = re.search(r"@@map\(\"(\w+)\"\)", m.group(2))
        if tm:
            table = tm.group(1)
        for line in m.group(2).splitlines():
            line = line.split("//")[0].strip()
            if not line or line.startswith("@@"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            name, typ = parts[0], parts[1]
            base = typ.rstrip("?[]")
            if base in models:
                rm = re.search(r"fields:\s*\[(\w+)", line)
                if rm:
                    relations.append({"field": rm.group(1), "target": base, "kind": "many_to_one"})
                    for f in fields:
                        if f["name"] == rm.group(1):
                            f["fk"], f["references"] = True, base
                    out["_prisma_fk"].append((m.group(1), rm.group(1), base))
                else:
                    relations.append({"field": name, "target": base,
                                      "kind": "one_to_many" if typ.endswith("[]") else "one_to_one"})
                continue
            fields.append({"name": name, "type": typ.rstrip("?") if base not in enums else base,
                           "pk": "@id" in line, "nullable": typ.endswith("?"), "unique": "@unique" in line})
        out["data_models"].append({"name": m.group(1), "table": table, "orm": "prisma",
                                   "file": rel(root, path), "fields": fields, "relations": relations})


def analyze_sql(root, path, src, out):
    for m in re.finditer(r"create\s+table\s+(?:if\s+not\s+exists\s+)?[`\"\[]?([\w.]+)[`\"\]]?\s*\(", src, re.I):
        depth, i = 1, m.end()
        while i < len(src) and depth:
            depth += {"(": 1, ")": -1}.get(src[i], 0)
            i += 1
        body = src[m.end():i - 1]
        table = m.group(1).split(".")[-1]
        fields, relations, pks = [], [], set()
        for part in re.split(r",(?![^()]*\))", body):
            p = part.strip()
            if not p:
                continue
            pk = re.match(r"(?:constraint\s+\w+\s+)?primary\s+key\s*\(([^)]*)\)", p, re.I)
            if pk:
                pks.update(x.strip(" `\"") for x in pk.group(1).split(","))
                continue
            fk = re.match(r"(?:constraint\s+\w+\s+)?foreign\s+key\s*\(([^)]*)\)\s*references\s+[`\"]?([\w.]+)",
                          p, re.I)
            if fk:
                col = fk.group(1).strip(" `\"")
                tgt = fk.group(2).split(".")[-1]
                relations.append({"field": col, "target": tgt, "kind": "many_to_one"})
                for f in fields:
                    if f["name"] == col:
                        f["fk"], f["references"] = True, tgt
                continue
            if re.match(r"(constraint|unique|check|index|key)\b", p, re.I):
                continue
            cm = re.match(r"[`\"\[]?(\w+)[`\"\]]?\s+([\w]+(?:\s*\([^)]*\))?(?:\s+varying(?:\([^)]*\))?)?)(.*)",
                          p, re.S)
            if not cm:
                continue
            rest = cm.group(3)
            f = {"name": cm.group(1), "type": cm.group(2).upper(),
                 "pk": bool(re.search(r"primary\s+key", rest, re.I)),
                 "nullable": not re.search(r"not\s+null|primary\s+key", rest, re.I),
                 "unique": bool(re.search(r"\bunique\b", rest, re.I))}
            ref = re.search(r"references\s+[`\"]?([\w.]+)", rest, re.I)
            if ref:
                f["fk"] = True
                f["references"] = ref.group(1).split(".")[-1]
                relations.append({"field": f["name"], "target": f["references"], "kind": "many_to_one"})
            fields.append(f)
        for f in fields:
            if f["name"] in pks:
                f["pk"] = True
                f["nullable"] = False
        out["data_models"].append({"name": table, "table": table, "orm": "sql", "file": rel(root, path),
                                   "fields": fields, "relations": relations})


def analyze_yaml(root, path, src, out):
    try:
        import yaml
    except ImportError:
        out["notes"].append("PyYAML not installed; skipped YAML infrastructure files")
        return
    try:
        docs = [d for d in yaml.safe_load_all(src) if isinstance(d, dict)]
    except Exception:  # noqa: BLE001
        return
    name = os.path.basename(path).lower()
    for d in docs:
        if "services" in d and isinstance(d["services"], dict) and ("compose" in name or "version" in d
                                                                     or name.startswith("docker")):
            for sname, svc in d["services"].items():
                svc = svc or {}
                build = svc.get("build")
                ctx = build.get("context") if isinstance(build, dict) else build
                env = svc.get("environment") or {}
                if isinstance(env, list):
                    env = dict(e.split("=", 1) if "=" in e else (e, "") for e in env)
                deps = svc.get("depends_on") or []
                if isinstance(deps, dict):
                    deps = list(deps)
                out["infrastructure"]["compose_services"].append({
                    "name": sname, "image": svc.get("image"), "build": ctx,
                    "build_dir": os.path.normpath(os.path.join(os.path.dirname(rel(root, path)), ctx))
                    if ctx else None,
                    "ports": [str(p) for p in svc.get("ports", [])], "depends_on": deps,
                    "env_refs": sorted({k for k, v in env.items() if v is not None}),
                    "env_values": {k: str(v) for k, v in env.items() if v is not None},
                    "networks": list(svc.get("networks") or []), "replicas":
                    (svc.get("deploy") or {}).get("replicas"), "file": rel(root, path)})
        elif "kind" in d and "apiVersion" in d:
            meta = d.get("metadata") or {}
            spec = d.get("spec") or {}
            item = {"kind": d["kind"], "name": meta.get("name"), "namespace": meta.get("namespace", "default"),
                    "file": rel(root, path)}
            tmpl = (spec.get("template") or {}).get("spec") or {}
            if tmpl.get("containers"):
                item["containers"] = [{"name": c.get("name"), "image": c.get("image"),
                                       "ports": [p.get("containerPort") for p in c.get("ports", [])]}
                                      for c in tmpl["containers"]]
                item["replicas"] = spec.get("replicas")
                item["labels"] = {**(((spec.get("template") or {}).get("metadata") or {}).get("labels") or {}),
                                  **(((spec.get("selector") or {}).get("matchLabels")) or {})}
            if d["kind"] == "Service":
                item["selector"] = spec.get("selector") or {}
                item["type"] = spec.get("type", "ClusterIP")
                item["ports"] = [p.get("port") for p in spec.get("ports", [])]
            if d["kind"] == "Ingress":
                backends = []
                for r in spec.get("rules", []) or []:
                    for p in ((r.get("http") or {}).get("paths") or []):
                        svc = ((p.get("backend") or {}).get("service") or {}).get("name") or \
                            (p.get("backend") or {}).get("serviceName")
                        backends.append({"host": r.get("host"), "path": p.get("path"), "service": svc})
                item["backends"] = backends
            out["infrastructure"]["k8s"].append(item)


# ============================================================ driver
def module_name(root, path):
    r = rel(root, path)
    parts = r[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    for prefix in ("src", "lib", "app_src"):
        if parts and parts[0] == prefix and len(parts) > 1:
            parts = parts[1:]
    return ".".join(parts)


def scan(root):
    out = {"root": os.path.abspath(root), "languages": Counter(), "files_scanned": 0, "classes": [],
           "data_models": [], "routes": [], "infrastructure": {"compose_services": [], "k8s": [],
                                                                "terraform": [], "dockerfiles": []},
           "external_integrations": defaultdict(set), "notes": [], "_py_imports": {}, "_c_imports": {},
           "_pkg_of_file": {}, "_transitions": [], "_sqla_rels": [], "_ef_sets": set(), "_prisma_fk": [], "manifests": {}}
    files = []
    for d, dirs, fs in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS and not x.startswith(".")]
        for f in fs:
            files.append(os.path.join(d, f))
    files.sort()
    if len(files) > 20000:
        out["notes"].append(f"{len(files)} files found; only the first 20000 were scanned")
        files = files[:20000]
    # EF Core DbSet names first
    for p in files:
        if p.endswith(".cs"):
            try:
                for m in re.finditer(r"DbSet<(\w+)>", open(p, encoding="utf-8", errors="ignore").read()):
                    out["_ef_sets"].add(m.group(1))
            except OSError:
                pass
    for p in files:
        try:
            if os.path.getsize(p) > MAX_FILE:
                continue
            src = open(p, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        r = rel(root, p)
        base = os.path.basename(p).lower()
        ext = os.path.splitext(p)[1].lower()
        lang = LANG_EXT.get(ext)
        is_test = bool(TEST_HINT.search(r))
        if lang:
            out["languages"][lang] += 1
            out["files_scanned"] += 1
            if not is_test:
                for key, rx in INTEGRATIONS.items():
                    if re.search(rx, src, re.I):
                        out["external_integrations"][key].add(r)
            if is_test:
                continue
            if lang == "python":
                PyAnalyzer(root, p, module_name(root, p), src, out).run()
            elif lang in ("java", "kotlin", "csharp", "typescript", "javascript", "go", "php", "scala", "swift"):
                if lang in ("javascript",) and (base.endswith(".min.js") or "bundle" in base):
                    continue
                analyze_c_like(root, p, lang, src, out)
        elif ext == ".prisma":
            analyze_prisma(root, p, src, out)
        elif ext == ".sql" and re.search(r"create\s+table", src, re.I):
            analyze_sql(root, p, src, out)
        elif ext in (".yml", ".yaml"):
            analyze_yaml(root, p, src, out)
        elif ext == ".tf":
            for m in re.finditer(r'^resource\s+"(\w+)"\s+"(\w+)"', src, re.M):
                out["infrastructure"]["terraform"].append({"type": m.group(1), "name": m.group(2), "file": r})
        elif base == "dockerfile" or base.startswith("dockerfile."):
            out["infrastructure"]["dockerfiles"].append({
                "file": r, "from": re.findall(r"^FROM\s+(\S+)", src, re.M | re.I),
                "expose": re.findall(r"^EXPOSE\s+(.+)$", src, re.M | re.I),
                "cmd": (re.findall(r"^(?:CMD|ENTRYPOINT)\s+(.+)$", src, re.M | re.I) or [None])[-1]})
        elif base in ("package.json", "requirements.txt", "pyproject.toml", "go.mod", "pom.xml",
                      "build.gradle", "build.gradle.kts", "composer.json", "gemfile", "cargo.toml") \
                or base.endswith(".csproj"):
            out["manifests"][r] = manifest_deps(base, src)
    finish(out)
    return out


def manifest_deps(base, src):
    try:
        if base in ("package.json", "composer.json"):
            j = json.loads(src)
            deps = list((j.get("dependencies") or j.get("require") or {}).keys())
            return {"name": j.get("name"), "dependencies": deps[:80]}
        if base == "requirements.txt":
            return {"dependencies": [re.split(r"[<>=\[; ]", l.strip())[0] for l in src.splitlines()
                                     if l.strip() and not l.startswith(("#", "-"))][:80]}
        if base == "go.mod":
            m = re.search(r"^module\s+(\S+)", src, re.M)
            return {"module": m.group(1) if m else None,
                    "dependencies": re.findall(r"^\s+([\w./-]+)\s+v", src, re.M)[:80]}
        if base == "pom.xml":
            return {"dependencies": re.findall(r"<artifactId>([^<]+)</artifactId>", src)[:80]}
        if base.endswith(".csproj"):
            return {"dependencies": re.findall(r'PackageReference\s+Include="([^"]+)"', src)[:80]}
        return {"dependencies": re.findall(r"^\s*[\"']?([\w.-]+)[\"']?\s*[=:]", src, re.M)[:80]}
    except Exception:  # noqa: BLE001
        return {}


def finish(out):
    known_classes = {c["name"] for c in out["classes"]}
    # ---- internal package graph
    file_pkg = {}
    edges = Counter()
    py_mods = set(out["_py_imports"])
    suffix = defaultdict(set)   # every dotted suffix of a module name -> modules ending with it
    for mname in py_mods:
        ps = mname.split(".")
        for k in range(len(ps)):
            suffix[".".join(ps[k:])].add(mname)
    for mod, imps in out["_py_imports"].items():
        for imp in imps:
            target = None
            parts = imp.split(".")
            for k in range(len(parts), 0, -1):
                cand = ".".join(parts[:k])
                hits = suffix.get(cand, set())
                if cand in py_mods:
                    target = cand
                elif len(hits) == 1:
                    target = next(iter(hits))
                elif len(hits) > 1:  # ambiguous: prefer the one sharing the importer's top package
                    same = [h for h in hits if h.split(".")[0] == mod.split(".")[0]]
                    target = same[0] if len(same) == 1 else None
                if target:
                    break
            if target and target != mod:
                edges[(mod, target)] += 1
    go_module = next((v.get("module") for k, v in out["manifests"].items() if k.endswith("go.mod")), None)
    pkg_files = defaultdict(list)
    for f, pkg in out["_pkg_of_file"].items():
        key = pkg or os.path.dirname(f) or "."
        file_pkg[f] = key
        pkg_files[key].append(f)
    known_pkgs = set(pkg_files)
    for f, imps in out["_c_imports"].items():
        src_pkg = file_pkg.get(f)
        for imp in imps:
            tgt = None
            if imp.startswith("."):
                path = os.path.normpath(os.path.join(os.path.dirname(f), imp)).replace(os.sep, "/")
                cands = [path, os.path.dirname(path)]
                tgt = next((c for c in cands if c in known_pkgs), None)
                if tgt is None:
                    tgt = next((file_pkg[k] for k in file_pkg
                                if os.path.splitext(k)[0] in (path, path + "/index")), None)
            elif go_module and imp.startswith(go_module):
                sub = imp[len(go_module):].strip("/")
                tgt = sub if sub in known_pkgs else None
            else:
                pk = imp.rsplit(".", 1)[0]
                tgt = imp if imp in known_pkgs else pk if pk in known_pkgs else None
            if tgt and tgt != src_pkg:
                edges[(src_pkg, tgt)] += 1
    modules = sorted(set(py_mods) | known_pkgs)
    out["modules"] = modules
    # ---- SQLAlchemy relationship() multiplicity: a list when the target table holds the FK to us
    fk_tables = defaultdict(set)   # model/class name -> tables it references
    for m in out["data_models"]:
        for f in m["fields"]:
            if f.get("references"):
                fk_tables[m["name"]].add(f["references"].split(".")[0])
    for attr, target, own_table, scalar in out["_sqla_rels"]:
        many = own_table in fk_tables.get(target, set()) and not scalar
        attr["type"] = f"list[{target}]" if many else target
    out["module_dependencies"] = [{"from": a, "to": b, "count": n} for (a, b), n in sorted(edges.items())]
    # ---- state candidates
    cands = []
    for c in out["classes"]:
        if c["kind"] == "enum" and re.search(r"(status|state|phase|stage|lifecycle)", c["name"], re.I):
            trans = []
            for t in out["_transitions"]:
                for s in t["sets"]:
                    if s.split(".")[0] == c["name"]:
                        trans.append({"event": t["function"], "to": s.split(".")[-1],
                                      "from": [x.split(".")[-1] for x in t["requires"] if x.split(".")[0] == c["name"]],
                                      "file": t["file"], "line": t["line"]})
            cands.append({"enum": c["name"], "values": c["enum_values"], "file": c["file"],
                          "observed_transitions": trans})
    out["state_candidates"] = cands
    out["status_assignments"] = out["_transitions"][:200]
    out["languages"] = dict(out["languages"].most_common())
    out["external_integrations"] = {k: sorted(v)[:15] for k, v in sorted(out["external_integrations"].items())}
    for k in [k for k in out if k.startswith("_")]:
        del out[k]
    for c in out["classes"]:
        for a in c["attributes"]:
            a.pop("_value", None)
            a.pop("_annos", None)
        for m in c["methods"]:
            m.pop("_annos", None)
    out["summary"] = {
        "classes": len(out["classes"]), "data_models": len(out["data_models"]), "routes": len(out["routes"]),
        "modules": len(out["modules"]), "compose_services": len(out["infrastructure"]["compose_services"]),
        "k8s_objects": len(out["infrastructure"]["k8s"]),
        "terraform_resources": len(out["infrastructure"]["terraform"]),
        "known_class_names_sample": sorted(known_classes)[:40]}


# ============================================================ draft spec
IMAGE_KINDS = [(r"postgres|mysql|mariadb|mssql|sqlserver|oracle|cockroach|mongo|cassandra|dynamo", "database"),
               (r"redis|memcache|valkey", "cache"), (r"rabbit|kafka|nats|activemq|sqs|pulsar|zookeeper", "queue"),
               (r"nginx|traefik|haproxy|envoy|caddy", "load_balancer"), (r"elastic|opensearch|solr|meili", "search"),
               (r"minio|localstack|azurite", "storage"), (r"keycloak|auth", "auth"),
               (r"prometheus|grafana|jaeger|zipkin|loki|otel", "monitoring")]


def image_kind(name, image):
    txt = f"{name} {image or ''}".lower()
    for rx, kind in IMAGE_KINDS:
        if re.search(rx, txt):
            return kind
    return "service"


def trim_packages(names, depth):
    split = {n: re.split(r"[./]", n) for n in names}
    lists = [v for v in split.values() if v and v != [""]]
    prefix = os.path.commonprefix(lists) if len(lists) > 1 else []
    return {n: ".".join((v[len(prefix):] or v[-1:])[:depth]) or n for n, v in split.items()}


def _name_variants(name):
    """Class/table name spellings to try when resolving a relation target (Merchant -> merchants...)."""
    base = name.split(".")[0]
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", base).lower()
    out = [base, base.lower(), snake]
    for w in (base.lower(), snake):
        out += [w + "s", w + "es", (w[:-1] + "ies") if w.endswith("y") else w]
    if base.lower().endswith("s"):
        out.append(base.lower()[:-1])
    return list(dict.fromkeys(out))


#: keys under which each diagram type keeps its clickable elements
_ELEMENT_KEYS = ("elements", "nodes", "classes", "entities", "states",
                 "participants", "use_cases", "actors")
#: pseudo-states and control nodes that are not clickable and need no description
_SILENT_KINDS = {"initial", "final", "fork", "join", "merge", "choice",
                 "history", "deep_history", "flow_final"}


def mark_missing_descriptions(spec):
    """Flag every draft element that has no `description` (no docstring was
    found) with a `_todo_description` note, so the gaps are visible in the spec
    instead of surfacing only as generator warnings. Returns how many were
    flagged. Docstrings that *were* copied in still need a read: they often say
    how the code works rather than what job the component does."""
    count = 0

    def walk(els):
        nonlocal count
        for el in els or []:
            if not isinstance(el, dict):
                continue
            if not el.get("description") and el.get("kind") not in _SILENT_KINDS:
                el["_todo_description"] = ("say in plain words what job this component "
                                           "does (and, if not obvious, why it exists)")
                count += 1
            walk(el.get("children"))
            if isinstance(el.get("detail"), dict):
                for k in _ELEMENT_KEYS:
                    walk(el["detail"].get(k))

    for d in spec.get("diagrams", []):
        for k in _ELEMENT_KEYS:
            walk(d.get(k))
    return count


def draft_spec(a, max_classes=30, depth=1):
    diagrams = []
    classes = a["classes"]
    names = {}
    for c in classes:
        names.setdefault(c["name"], c)
    # ---------------- class diagram(s)
    if classes:
        rels = []
        for c in names.values():
            for b in c["bases"]:
                b = b.split(".")[-1]
                if b in names and b != c["name"]:
                    kind = "implementation" if names[b]["kind"] == "interface" and c["kind"] != "interface" \
                        else "inheritance"
                    rels.append({"from": c["name"], "to": b, "kind": kind})
            for i in c["interfaces"]:
                i = i.split(".")[-1]
                if i in names:
                    rels.append({"from": c["name"], "to": i, "kind": "implementation"})
            linked = set()
            for at in c["attributes"]:
                for t in simple_type(at.get("type")):
                    if t in names and t != c["name"] and t not in linked:
                        linked.add(t)
                        kind = "dependency" if names[t]["kind"] == "enum" else "association"
                        r = {"from": c["name"], "to": t, "kind": kind}
                        if kind == "association":
                            r["to_mult"] = "*" if is_collection(at.get("type")) else "1"
                            r["label"] = at["name"]
                        rels.append(r)
        # A->B and B->A plain associations are one bidirectional association in UML
        merged, by_pair = [], {}
        for r in rels:
            if r["kind"] == "association":
                back = by_pair.get((r["to"], r["from"]))
                if back is not None:
                    back.update(kind="bidirectional", from_mult=r.get("to_mult", ""),
                                label=f"{back.get('label', '')} / {r.get('label', '')}".strip(" /"))
                    continue
                by_pair[(r["from"], r["to"])] = r
            merged.append(r)
        rels = merged
        degree = Counter()
        for r in rels:
            degree[r["from"]] += 1
            degree[r["to"]] += 1
        chosen = sorted(names, key=lambda n: (-degree[n], n))[:max_classes]
        chosen_set = set(chosen)

        def cls_spec(n):
            c = names[n]
            spec = {"name": n}
            if c.get("description"):
                spec["description"] = c["description"]
            if c["kind"] in ("interface", "abstract", "enum"):
                spec["stereotype"] = c["kind"]
            elif c["kind"] == "struct":
                spec["stereotype"] = "struct"
            spec["attributes"] = [{k: v for k, v in at.items() if v not in ("", None, False)}
                                  for at in c["attributes"] if not at["name"].startswith("__")][:12]
            spec["methods"] = [{k: v for k, v in m.items() if v not in ("", None, False, [])}
                               for m in c["methods"]][:12]
            if c["kind"] == "enum":
                spec["values"] = c["enum_values"][:15]
            return spec
        diagrams.append({"type": "class", "name": "Class model",
                         "classes": [cls_spec(n) for n in chosen],
                         "relationships": [r for r in rels if r["from"] in chosen_set and r["to"] in chosen_set]})
    # ---------------- ER
    if a["data_models"]:
        by_name = {}
        for m in a["data_models"]:
            by_name[m["name"]] = m
            by_name.setdefault(m["table"], m)
        ents, rels, seen_rel = [], [], set()
        for m in a["data_models"]:
            if any(e["id"] == m["name"] for e in ents):
                continue
            attrs = []
            for f in m["fields"]:
                ftype = f.get("type", "")
                if ftype == "FK":  # show the referenced key's real type when we can find it
                    ftype = ""
                    for cand in _name_variants(str(f.get("references", ""))):
                        ref = by_name.get(cand)
                        if ref:
                            ftype = next((x.get("type", "") for x in ref["fields"] if x.get("pk")), "")
                            ftype = {"BIGSERIAL": "BIGINT", "SERIAL": "INTEGER", "SMALLSERIAL": "SMALLINT",
                                     "AutoField": "Integer", "BigAutoField": "BigInteger"}.get(ftype.upper()
                                                                                             if "SERIAL" in ftype.upper()
                                                                                             else ftype, ftype)
                            break
                attrs.append({k: v for k, v in {"name": f["name"], "type": ftype, "pk": f.get("pk"),
                                                "fk": f.get("fk"), "unique": f.get("unique"),
                                                "nullable": f.get("nullable", True)}.items() if v is not None})
            ent = {"id": m["name"], "label": m["table"], "attributes": attrs}
            if m.get("description"):
                ent["description"] = m["description"]
            ents.append(ent)
        for m in a["data_models"]:
            for r in m["relations"]:
                tgt = None
                for cand in _name_variants(r["target"]):
                    tgt = by_name.get(cand)
                    if tgt:
                        break
                if not tgt:
                    continue
                pk = next((f["name"] for f in tgt["fields"] if f.get("pk")), None)
                if r["kind"] in ("many_to_one", "one_to_one"):
                    key = (tgt["name"], m["name"], r["field"])
                    if key in seen_rel:
                        continue
                    seen_rel.add(key)
                    field = next((f for f in m["fields"] if f["name"] == r["field"]), {})
                    rel_spec = {"from": tgt["name"], "to": m["name"],
                                "from_card": "0..1" if field.get("nullable") else "1",
                                "to_card": "0..1" if r["kind"] == "one_to_one" else "0..*"}
                    if pk and field:
                        rel_spec.update(from_field=pk, to_field=r["field"])
                    rels.append(rel_spec)
                elif r["kind"] == "many_to_many":
                    key = tuple(sorted((m["name"], tgt["name"])))
                    if key in seen_rel:
                        continue
                    seen_rel.add(key)
                    rels.append({"from": m["name"], "to": tgt["name"], "from_card": "0..*", "to_card": "0..*",
                                 "label": r["field"]})
        diagrams.append({"type": "er", "name": "Data model", "entities": ents, "relationships": rels})
    # ---------------- component (package dependencies)
    deps = a.get("module_dependencies", [])
    if deps:
        mods = sorted({d["from"] for d in deps} | {d["to"] for d in deps})
        short = trim_packages(mods, depth)
        comps = sorted(set(short.values()))
        agg = Counter()
        for d in deps:
            s, t = short[d["from"]], short[d["to"]]
            if s != t:
                agg[(s, t)] += d["count"]
        if len(comps) > 1:
            els = [{"id": f"pkg_{re.sub(r'[^A-Za-z0-9]', '_', c)}", "label": c} for c in comps]
            ids = {c: e["id"] for c, e in zip(comps, els)}
            ext, ext_conns = [], []
            for key in sorted(a.get("external_integrations", {})):
                if key in ("graphql", "grpc", "smtp"):
                    continue
                eid = f"ext_{re.sub(r'[^A-Za-z0-9]', '_', key)}"
                users = set()
                for f in a["external_integrations"][key]:
                    fmod = re.sub(r"\.\w+$", "", f).replace("/", ".")
                    for full, sh in short.items():
                        if fmod == full or fmod.endswith("." + full) or full.endswith(fmod) \
                                or fmod.startswith(full + "."):
                            users.add(sh)
                if not users:
                    continue  # integration lives outside the modules shown; keep the diagram focused
                ext.append({"id": eid, "label": key, "stereotype": "external"})
                ext_conns += [{"from": ids[u], "to": eid} for u in sorted(users) if u in ids]
            diagrams.append({"type": "component", "name": "Module dependencies",
                             "elements": [{"id": "system", "label": os.path.basename(a["root"]) or "System",
                                           "kind": "subsystem", "children": els}] + ext,
                             "connections": [{"from": ids[s], "to": ids[t]} for (s, t) in agg] + ext_conns})
    # ---------------- architecture + deployment from compose / k8s
    infra = a["infrastructure"]
    if infra["compose_services"]:
        svcs = infra["compose_services"]
        sid = {s["name"]: f"svc_{re.sub(r'[^A-Za-z0-9]', '_', s['name'])}" for s in svcs}
        els, conns = [], []
        for s in svcs:
            els.append({"id": sid[s["name"]], "label": s["name"], "kind": image_kind(s["name"], s["image"]),
                        **({"technology": s["image"]} if s["image"] else {})})
        pairs = set()
        for s in svcs:
            for dep in s["depends_on"]:
                if dep in sid:
                    pairs.add((s["name"], dep))
            for v in s.get("env_values", {}).values():
                for other in sid:
                    if other != s["name"] and re.search(rf"(^|[/@:]){re.escape(other)}([:/]|$)", v):
                        pairs.add((s["name"], other))
        conns = [{"from": sid[x], "to": sid[y]} for x, y in sorted(pairs)]
        public = [s for s in svcs if s["ports"]]
        top = [{"id": "user", "label": "User", "kind": "user"}]
        entry = [s for s in public if image_kind(s["name"], s["image"]) in ("load_balancer",)] or \
            [s for s in public if image_kind(s["name"], s["image"]) == "service"][:2]
        conns = [{"from": "user", "to": sid[s["name"]], "protocol": "HTTP"} for s in entry] + conns
        ext_nodes = []
        for key, files in a.get("external_integrations", {}).items():
            if key in ("postgresql", "mysql", "mongodb", "redis", "rabbitmq", "kafka", "elasticsearch",
                       "graphql", "grpc", "celery"):
                continue
            eid = f"ext_{re.sub(r'[^A-Za-z0-9]', '_', key)}"
            users = [s["name"] for s in svcs if s.get("build_dir") and
                     any(f.startswith(s["build_dir"].rstrip('/') + "/") or s["build_dir"] in (".", "")
                         for f in files)]
            ext_nodes.append({"id": eid, "label": key, "kind": "external"})
            for u in users:
                conns.append({"from": sid[u], "to": eid})
        diagrams.append({"type": "architecture", "name": "System architecture (from docker-compose)",
                         "elements": top + [{"id": "compose", "label": "Docker Compose stack", "kind": "boundary",
                                             "children": els}] + ext_nodes,
                         "connections": conns})
        dep_children = []
        for s in svcs:
            dep_children.append({"id": f"ct_{sid[s['name']]}", "label": s["name"], "kind": "execution_environment",
                                 "stereotype": "container",
                                 "children": [{"id": f"art_{sid[s['name']]}",
                                               "label": s["image"] or f"built from {s['build']}",
                                               "kind": "artifact"}]})
        diagrams.append({"type": "deployment", "name": "Deployment (docker-compose)",
                         "elements": [{"id": "host", "label": "Docker host", "kind": "node", "stereotype": "device",
                                       "children": dep_children}],
                         "connections": [{"from": f"ct_{sid[x]}", "to": f"ct_{sid[y]}"} for x, y in sorted(pairs)]})
    if infra["k8s"]:
        workloads = [k for k in infra["k8s"] if k.get("containers")]
        services = [k for k in infra["k8s"] if k["kind"] == "Service"]
        ingresses = [k for k in infra["k8s"] if k["kind"] == "Ingress"]
        nss = sorted({k["namespace"] for k in workloads} or {"default"})
        wid = {w["name"]: f"wl_{re.sub(r'[^A-Za-z0-9]', '_', str(w['name']))}" for w in workloads}
        children = {ns: [] for ns in nss}
        for w in workloads:
            children[w["namespace"]].append({
                "id": wid[w["name"]], "label": f"{w['name']} ({w['kind']}, x{w.get('replicas') or 1})",
                "kind": "execution_environment", "stereotype": "pod",
                "children": [{"id": f"{wid[w['name']]}_{i}", "label": c["image"] or c["name"], "kind": "artifact"}
                             for i, c in enumerate(w["containers"])]})
        conns = []
        svc_target = {}
        for s in services:
            for w in workloads:
                if s.get("selector") and all(w.get("labels", {}).get(k) == v for k, v in s["selector"].items()):
                    svc_target[s["name"]] = wid[w["name"]]
        els = [{"id": "cluster", "label": "Kubernetes cluster", "kind": "node", "children": [
            {"id": f"ns_{re.sub(r'[^A-Za-z0-9]', '_', ns)}", "label": f"namespace: {ns}",
             "kind": "execution_environment", "children": children[ns]} for ns in nss]}]
        if ingresses:
            els.insert(0, {"id": "client", "label": "Client", "kind": "device"})
            for ing in ingresses:
                for b in ing.get("backends", []):
                    if b["service"] in svc_target:
                        conns.append({"from": "client", "to": svc_target[b["service"]],
                                      "protocol": f"HTTPS {b.get('host') or ''}{b.get('path') or ''}".strip()})
        diagrams.append({"type": "deployment", "name": "Kubernetes deployment",
                         "elements": els, "connections": conns})
    # ---------------- state machines
    for sc in a.get("state_candidates", [])[:5]:
        vals = sc["values"]
        if not vals:
            continue
        states = [{"id": "__init", "kind": "initial"}] + [{"id": v, "label": v} for v in vals]
        trans = [{"from": "__init", "to": vals[0]}]
        seen = set()
        for t in sc["observed_transitions"]:
            if t["to"] not in vals:
                continue
            for f in (t["from"] or []):
                if f in vals and (f, t["to"], t["event"]) not in seen:
                    seen.add((f, t["to"], t["event"]))
                    trans.append({"from": f, "to": t["to"], "event": t["event"]})
        spec = {"type": "state", "name": f"{sc['enum']} lifecycle", "states": states, "transitions": trans}
        unknown = sorted({f"{t['event']}->{t['to']}" for t in sc["observed_transitions"] if not t["from"]})
        if unknown:
            spec["_todo"] = ("Transitions whose source state could not be inferred were omitted; "
                             "read the code and add them: " + ", ".join(unknown)[:500])
        if len(trans) <= 1:
            spec["_todo"] = (spec.get("_todo", "") + " No transitions were inferred - the diagram only lists the "
                             "states until you add transitions from the code.").strip()
        diagrams.append(spec)
    return {"title": os.path.basename(a["root"]), "diagrams": diagrams}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path")
    ap.add_argument("--out", help="write the full analysis JSON here (default: stdout summary only)")
    ap.add_argument("--draft-spec", help="write a starter diagram spec here")
    ap.add_argument("--max-classes", type=int, default=30)
    ap.add_argument("--depth", type=int, default=1, help="package depth for the component diagram")
    args = ap.parse_args()
    if not os.path.isdir(args.path):
        sys.exit(f"not a directory: {args.path}")
    analysis = scan(args.path)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(analysis, fh, indent=1, default=list)
    if args.draft_spec:
        spec = draft_spec(analysis, args.max_classes, args.depth)
        todo = mark_missing_descriptions(spec)
        with open(args.draft_spec, "w", encoding="utf-8") as fh:
            json.dump(spec, fh, indent=1)
        if todo:
            print(f"draft spec: {todo} element(s) have no docstring to describe them; each is "
                  "marked with \"_todo_description\". Write its job in plain words before "
                  "generating.", file=sys.stderr)
    s = analysis["summary"]
    print(json.dumps({"languages": analysis["languages"], **s,
                      "routes_sample": [f"{r['method']} {r['path']} -> {r['handler']}"
                                        for r in analysis["routes"][:25]],
                      "integrations": list(analysis["external_integrations"]),
                      "state_candidates": [c["enum"] for c in analysis["state_candidates"]],
                      "notes": analysis["notes"]}, indent=1))


if __name__ == "__main__":
    main()
