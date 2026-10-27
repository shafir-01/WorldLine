"""Scenario validation: structure (JSON Schema), predicate DSL, references, and smells.

``scenarios/schema/scenario.schema.json`` is the single source of truth for the
*shape* of a scenario. This module interprets the subset of JSON Schema draft
2020-12 that the file uses (``$ref`` to ``$defs``, ``type``, ``enum``, ``const``,
``required``, ``properties``, ``patternProperties``, ``additionalProperties``,
``items``, ``minLength``, ``pattern``, ``minimum``, ``maximum``, ``anyOf``,
``oneOf``) so no third-party validator is needed, then layers the checks a schema
cannot express:

* every ``when`` is parsed with :func:`parse_predicate`; unknown functions, flags
  and filters come back with a "did you mean" suggestion, and ``payload.*``
  filters are checked against the payload fields the injects actually carry;
* referential integrity: ``actor.role`` -> a role, ``payload.corrects`` and
  ``payload.acknowledges`` -> an inject id, ``transition.roles`` -> roles, and
  ``invariants`` -> ids declared in ``invariants/*.yaml`` (when a directory is given);
* duplicate ids (roles, actors, rules, effective inject ids, transitions) and
  duplicate YAML keys (which ``yaml.safe_load`` would silently collapse);
* warnings for suspicious but loadable things: a rule that references a kind no
  inject has, a role that can never receive anything, an actor whose role cannot
  see the kinds its rule counts, injects after the horizon, an unused outage.

Every issue carries a YAML path such as ``actors[0].decision_rules[1].when`` and,
when the source text is available, a 1-based line number.

Entry points: :func:`validate_file`, :func:`validate_text`, :func:`validate_data`.
"""
from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import yaml

from .predicate import (
    _EVENT_FIELDS, _FLAGS, _FUNCTIONS, BoolOp, Call, Compare, Not, PredicateError, parse_predicate,
    referenced_kinds,
)
from .scenario import parse_ns, scenario_from_dict

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "scenarios" / "schema" / "scenario.schema.json"

ERROR = "error"
WARNING = "warning"


# ============================================================================ results

@dataclass(frozen=True)
class Issue:
    severity: str          # "error" | "warning"
    path: str              # YAML path, e.g. "actors[0].decision_rules[1].when"; "" is the document root
    message: str
    line: int | None = None

    def __str__(self) -> str:
        return f"{self.path or '<root>'}: {self.message}"

    def to_dict(self) -> dict:
        return {"severity": self.severity, "path": self.path, "line": self.line, "message": self.message}


@dataclass
class ValidationReport:
    source: str | None = None
    issues: list[Issue] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def messages(self, severity: str | None = None) -> list[str]:
        return [str(i) for i in self.issues if severity is None or i.severity == severity]

    def to_dict(self) -> dict:
        return {"source": self.source, "ok": self.ok, "errors": len(self.errors), "warnings": len(self.warnings),
                "issues": [i.to_dict() for i in self.issues], "summary": self.summary}


# ============================================================================ helpers

def _child(path: str, key: str | int) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    return f"{path}.{key}" if path else str(key)


def _suggest(word: str, candidates: Iterable[str], cutoff: float = 0.6) -> str | None:
    cands = sorted({str(c) for c in candidates})
    m = difflib.get_close_matches(str(word), cands, n=1, cutoff=cutoff)
    return m[0] if m else None


def _did_you_mean(word: str, candidates: Iterable[str]) -> str:
    s = _suggest(word, candidates)
    return f" (did you mean '{s}'?)" if s else ""


def _suggest_payload(path: str, candidates: Iterable[str]) -> str | None:
    """Closest payload path, compared without the shared 'payload.' prefix (which would inflate similarity)."""
    strip = {c[len("payload."):]: c for c in candidates if c.startswith("payload.")}
    s = _suggest(path[len("payload."):] if path.startswith("payload.") else path, strip)
    return strip[s] if s else None


def _fmt_ns(ns: int) -> str:
    if ns % 3_600_000_000_000 == 0 and ns:
        return f"{ns // 3_600_000_000_000}h"
    if ns % 1_000_000_000 == 0:
        return f"{ns // 1_000_000_000}s"
    return f"{ns / 1e9:g}s"


def _try_ns(value: Any) -> int | None:
    try:
        return parse_ns(value)
    except (TypeError, ValueError):
        return None


@lru_cache(maxsize=1)
def load_schema() -> dict:
    """The scenario JSON Schema (draft 2020-12) shipped in scenarios/schema/."""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


# ============================================================================ structural (JSON Schema subset)

_TYPE_NAMES = {"object": "a mapping", "array": "a list", "string": "a string", "integer": "an integer",
               "number": "a number", "boolean": "true/false", "null": "null"}


def _is_type(value: Any, t: str) -> bool:
    if t == "object":
        return isinstance(value, dict)
    if t == "array":
        return isinstance(value, list)
    if t == "string":
        return isinstance(value, str)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "null":
        return value is None
    if t == "integer":
        return (isinstance(value, int) and not isinstance(value, bool)) or (
            isinstance(value, float) and value.is_integer())
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    raise ValueError(f"schema uses unsupported type {t!r}")


def _resolve(schema: dict, root: dict) -> dict:
    while "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/"):
            raise ValueError(f"only local $ref is supported, got {ref!r}")
        node: Any = root
        for part in ref[2:].split("/"):
            node = node[part]
        schema = {**node, **{k: v for k, v in schema.items() if k != "$ref"}}
    return schema


def _short(value: Any) -> str:
    r = repr(value)
    return r if len(r) <= 60 else r[:57] + "..."


def _validate_node(value: Any, schema: dict, root: dict, path: str, out: list[Issue]) -> None:
    schema = _resolve(schema, root)

    for key in ("anyOf", "oneOf"):
        if key in schema:
            matches = 0
            for branch in schema[key]:
                trial: list[Issue] = []
                _validate_node(value, branch, root, path, trial)
                if not trial:
                    matches += 1
            if matches == 0 or (key == "oneOf" and matches > 1):
                what = schema.get("description") or "one of the allowed forms"
                out.append(Issue(ERROR, path, f"invalid value {_short(value)}: expected {what[0].lower() + what[1:]}"))
                return

    if "const" in schema and value != schema["const"]:
        out.append(Issue(ERROR, path, f"must be {schema['const']!r}, got {_short(value)}"))
        return
    if "enum" in schema and value not in schema["enum"]:
        allowed = ", ".join(repr(x) for x in schema["enum"])
        hint = _did_you_mean(value, [str(x) for x in schema["enum"]]) if isinstance(value, str) else ""
        out.append(Issue(ERROR, path, f"invalid value {_short(value)}{hint}; allowed: {allowed}"))
        return

    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(value, t) for t in types):
            want = " or ".join(_TYPE_NAMES.get(t, t) for t in types)
            got = "null" if value is None else type(value).__name__
            got = {"dict": "a mapping", "list": "a list", "str": "a string", "int": "an integer",
                   "float": "a number", "bool": "a boolean"}.get(got, got)
            out.append(Issue(ERROR, path, f"expected {want}, got {got} {_short(value)}".rstrip()))
            return

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            out.append(Issue(ERROR, path, "must not be empty"))
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append(Issue(ERROR, path, f"{_short(value)} does not match pattern {schema['pattern']}"))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(Issue(ERROR, path, f"must be >= {schema['minimum']}, got {value}"))
        if "maximum" in schema and value > schema["maximum"]:
            out.append(Issue(ERROR, path, f"must be <= {schema['maximum']}, got {value}"))

    if isinstance(value, dict):
        props = schema.get("properties", {})
        pattern_props = schema.get("patternProperties", {})
        for req in schema.get("required", []):
            if req not in value:
                out.append(Issue(ERROR, path, f"missing required key '{req}'"))
        for k, v in value.items():
            sub = _child(path, str(k))
            if k in props:
                _validate_node(v, props[k], root, sub, out)
                continue
            matched = [s for pat, s in pattern_props.items() if isinstance(k, str) and re.search(pat, k)]
            if matched:
                for s in matched:
                    _validate_node(v, s, root, sub, out)
                continue
            extra = schema.get("additionalProperties", True)
            if extra is False:
                hint = _did_you_mean(str(k), props)
                allowed = ", ".join(props)
                out.append(Issue(ERROR, sub, f"unknown key '{k}'{hint}; allowed here: {allowed}"))
            elif isinstance(extra, dict):
                _validate_node(v, extra, root, sub, out)

    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            _validate_node(item, schema["items"], root, _child(path, i), out)


def validate_structure(data: Any, schema: dict | None = None) -> list[Issue]:
    """Check ``data`` against the scenario JSON Schema. Returns issues (errors only)."""
    schema = schema or load_schema()
    out: list[Issue] = []
    _validate_node(data, schema, schema, "", out)
    return out


# ============================================================================ YAML source map

def _source_map(text: str) -> tuple[dict[str, int], list[Issue]]:
    """Map YAML paths to 1-based line numbers and report duplicate mapping keys."""
    lines: dict[str, int] = {}
    issues: list[Issue] = []
    try:
        node = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        return lines, issues

    def walk(n: Any, path: str) -> None:
        if isinstance(n, yaml.MappingNode):
            seen: dict[str, int] = {}
            for k, v in n.value:
                key = str(k.value)
                sub = _child(path, key)
                line = k.start_mark.line + 1
                if key in seen:
                    issues.append(Issue(ERROR, sub, f"duplicate key '{key}' (first defined on line {seen[key]}); "
                                                    "YAML keeps only the last one", line))
                seen[key] = line
                lines.setdefault(sub, line)
                walk(v, sub)
        elif isinstance(n, yaml.SequenceNode):
            for i, item in enumerate(n.value):
                sub = _child(path, i)
                lines.setdefault(sub, item.start_mark.line + 1)
                walk(item, sub)

    if node is not None:
        lines[""] = node.start_mark.line + 1
        walk(node, "")
    return lines, issues


def _line_for(path: str, lines: dict[str, int]) -> int | None:
    p = path
    while True:
        if p in lines:
            return lines[p]
        if not p:
            return None
        cut = max(p.rfind("."), p.rfind("["))
        p = p[:cut] if cut > 0 else ""


# ============================================================================ semantic checks

def _payload_paths(payload: Any, prefix: str = "payload") -> set[str]:
    out: set[str] = set()
    if isinstance(payload, dict):
        for k, v in payload.items():
            p = f"{prefix}.{k}"
            out.add(p)
            out |= _payload_paths(v, p)
    return out


def _calls(node: Any) -> list[tuple[Call, Compare | None]]:
    if isinstance(node, Compare):
        return [(node.call, node)]
    if isinstance(node, Call):
        return [(node, None)]
    if isinstance(node, Not):
        return _calls(node.inner)
    if isinstance(node, BoolOp):
        return [c for item in node.items for c in _calls(item)]
    return []


@dataclass
class _World:
    """Facts about a scenario document that the semantic checks need (built defensively)."""

    horizon_ns: int | None = None
    epoch_ns: int = 0
    role_ids: list[str] = field(default_factory=list)
    roles: dict[str, dict] = field(default_factory=dict)
    inject_ids: list[str] = field(default_factory=list)          # effective ids, in order
    injects: list[tuple[int, dict, str]] = field(default_factory=list)   # (index, raw, effective id)
    kinds: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)
    labels: set[str] = field(default_factory=set)
    paths_by_kind: dict[str, set[str]] = field(default_factory=dict)


def _build_world(data: dict) -> _World:
    w = _World()
    clock = data.get("clock") if isinstance(data.get("clock"), dict) else {}
    w.horizon_ns = _try_ns(clock.get("horizon_ns")) if "horizon_ns" in clock else None
    w.epoch_ns = _try_ns(clock.get("epoch_ns", 0)) or 0
    for r in data.get("roles") or []:
        if isinstance(r, dict) and isinstance(r.get("id"), str):
            w.role_ids.append(r["id"])
            w.roles.setdefault(r["id"], r)
    counters: dict[str, int] = {}
    for i, inj in enumerate(data.get("injects") or []):
        if not isinstance(inj, dict) or not isinstance(inj.get("kind"), str):
            continue
        kind = inj["kind"]
        counters[kind] = counters.get(kind, 0) + 1
        eid = str(inj["id"]) if inj.get("id") is not None else f"{kind}-{counters[kind]:03d}"
        w.inject_ids.append(eid)
        w.injects.append((i, inj, eid))
        w.kinds.add(kind)
        if isinstance(inj.get("source_id"), str):
            w.sources.add(inj["source_id"])
        labels = inj.get("policy_labels") or []
        if isinstance(labels, list):
            w.labels |= {str(x) for x in labels}
        w.paths_by_kind.setdefault(kind, set()).update(_payload_paths(inj.get("payload") or {}))
    return w


def _visible(role: dict, labels: list) -> bool:
    vis = role.get("visibility")
    vis = ["*"] if not vis else [str(x) for x in vis] if isinstance(vis, list) else ["*"]
    if "*" in vis:
        return True
    return all(str(lbl) in vis for lbl in labels)


def check_predicate(text: str, path: str, world: _World | None = None) -> list[Issue]:
    """Parse one ``when`` expression and check it against the scenario's kinds and payload fields."""
    out: list[Issue] = []
    world = world or _World()
    all_paths = set().union(*world.paths_by_kind.values()) if world.paths_by_kind else set()
    try:
        pred = parse_predicate(text)
    except PredicateError as exc:
        msg = str(exc)
        m = re.search(r"unknown function '([^']+)'", msg)
        if m:
            name = m.group(1)
            out.append(Issue(ERROR, path, f"unknown function '{name}'{_did_you_mean(name, _FUNCTIONS)}; "
                                          f"functions: {', '.join(sorted(_FUNCTIONS))}"))
            return out
        m = re.search(r"unknown flag '([^']+)'", msg)
        if m:
            name = m.group(1)
            sugg = _suggest(name, _FLAGS)
            if sugg:
                out.append(Issue(ERROR, path, f"unknown flag '{name}' (did you mean '{sugg}'?)"))
            else:
                out.append(Issue(ERROR, path, f"unknown flag '{name}'; flags: {', '.join(sorted(_FLAGS))} "
                                              "(a filter needs an operator, e.g. kind='sighting')"))
            return out
        m = re.search(r"unknown field '([^']+)'", msg)
        if m:
            name = m.group(1)
            cands = set(_EVENT_FIELDS) | all_paths | {p.split(".", 1)[1] for p in all_paths}
            sugg = _suggest(name, cands)
            if sugg and not sugg.startswith("payload.") and sugg not in _EVENT_FIELDS:
                sugg = f"payload.{sugg}"
            hint = f" (did you mean '{sugg}'?)" if sugg else ""
            extra = "; within_ns and ack_kind are only valid inside acknowledged()" \
                if name in ("within_ns", "ack_kind") else \
                f"; filters are event fields ({', '.join(sorted(_EVENT_FIELDS))}) or payload.<field>"
            out.append(Issue(ERROR, path, f"unknown filter '{name}'{hint}{extra}"))
            return out
        msg = re.sub(r" in '.*'$| in \".*\"$", "", msg)
        out.append(Issue(ERROR, path, f"predicate syntax error: {msg}"))
        return out

    known_kinds = world.kinds
    for call, cmp in _calls(pred.ast):
        kinds_here = {f.value for f in call.filters if f.field == "kind" and f.op == "==" and isinstance(f.value, str)}
        if cmp is not None and call.name in ("exists", "acknowledged"):
            out.append(Issue(WARNING, path, f"{call.name}(...) is a boolean; comparing it with a number "
                                            f"('{cmp.op} {cmp.value}') is always false. Use it bare, or use count(...)"))
        for f in call.filters:
            if f.field == "kind" and isinstance(f.value, str) and known_kinds and f.value not in known_kinds:
                out.append(Issue(WARNING, path, f"kind '{f.value}' never occurs in injects"
                                                f"{_did_you_mean(f.value, known_kinds)}; this filter matches nothing"))
            if not f.field.startswith("payload.") or not world.paths_by_kind:
                continue
            kinds_known = {k for k in kinds_here if k in world.paths_by_kind}
            scope = set().union(*(world.paths_by_kind[k] for k in kinds_known)) if kinds_known else all_paths
            if f.field in scope:
                continue
            sugg = _suggest_payload(f.field, scope) or _suggest_payload(f.field, all_paths)
            kinds_here = kinds_known
            where = f"inject of kind {', '.join(repr(k) for k in sorted(kinds_here))}" if kinds_here else "inject"
            if sugg:
                out.append(Issue(ERROR, path, f"unknown filter '{f.field}' (did you mean '{sugg}'?); "
                                              f"no {where} carries that payload field"))
            else:
                out.append(Issue(WARNING, path, f"filter '{f.field}': no {where} carries that payload field, "
                                                "so the comparison is false for every event"))
        if call.name == "acknowledged":
            ak = str(call.kwargs.get("ack_kind", "ack"))
            if known_kinds and ak not in known_kinds:
                out.append(Issue(WARNING, path, f"acknowledged(): ack kind '{ak}' never occurs in injects"
                                                f"{_did_you_mean(ak, known_kinds)}"))
        if call.flags and known_kinds and "correction" not in known_kinds:
            out.append(Issue(WARNING, path, f"flag {'/'.join(call.flags)} has no effect: no inject of kind "
                                            "'correction' exists"))
    return out


def _semantic(data: dict, invariant_ids: set[str] | None) -> list[Issue]:
    out: list[Issue] = []
    w = _build_world(data)

    def dupes(items: list[tuple[str, str]], what: str) -> None:
        seen: dict[str, str] = {}
        for p, ident in items:
            if ident in seen:
                out.append(Issue(ERROR, p, f"duplicate {what} id '{ident}' (also at {seen[ident]})"))
            else:
                seen[ident] = p

    # ---- roles
    roles = data.get("roles") or []
    dupes([(_child(_child("roles", i), "id"), r["id"]) for i, r in enumerate(roles)
           if isinstance(r, dict) and isinstance(r.get("id"), str)], "role")
    for i, r in enumerate(roles if isinstance(roles, list) else []):
        if not isinstance(r, dict) or not isinstance(r.get("id"), str):
            continue
        p = _child("roles", i)
        if w.injects and not any(_visible(r, inj.get("policy_labels") or []) for _, inj, _ in w.injects):
            out.append(Issue(WARNING, p, f"role '{r['id']}' never receives anything: its visibility "
                                         f"{r.get('visibility')} excludes every inject"))
        dp = (r.get("delivery") or {}).get("drop_probability") if isinstance(r.get("delivery"), dict) else None
        if isinstance(dp, (int, float)) and not isinstance(dp, bool) and dp >= 1:
            out.append(Issue(WARNING, _child(_child(p, "delivery"), "drop_probability"),
                             f"role '{r['id']}' never receives anything: drop_probability is 1"))
        vis = r.get("visibility")
        if isinstance(vis, list) and w.labels:
            for j, lbl in enumerate(vis):
                if isinstance(lbl, str) and lbl != "*" and lbl not in w.labels:
                    out.append(Issue(WARNING, _child(_child(p, "visibility"), j),
                                     f"label '{lbl}' is never carried by any inject{_did_you_mean(lbl, w.labels)}"))

    # ---- actors and rules
    actors = data.get("actors") or []
    actors = actors if isinstance(actors, list) else []
    dupes([(_child(_child("actors", i), "id"), a["id"]) for i, a in enumerate(actors)
           if isinstance(a, dict) and isinstance(a.get("id"), str)], "actor")
    rule_ids: list[tuple[str, str]] = []
    n_rules = 0
    for i, a in enumerate(actors):
        if not isinstance(a, dict):
            continue
        ap = _child("actors", i)
        role_id = a.get("role")
        if isinstance(role_id, str) and role_id not in w.role_ids:
            out.append(Issue(ERROR, _child(ap, "role"), f"unknown role '{role_id}'{_did_you_mean(role_id, w.role_ids)}"
                                                        f"; roles: {', '.join(w.role_ids) or '(none declared)'}"))
        role = w.roles.get(role_id) if isinstance(role_id, str) else None
        rules = a.get("decision_rules") or []
        for j, rule in enumerate(rules if isinstance(rules, list) else []):
            if not isinstance(rule, dict):
                continue
            n_rules += 1
            rp = _child(_child(ap, "decision_rules"), j)
            if isinstance(rule.get("id"), str):
                rule_ids.append((_child(rp, "id"), rule["id"]))
            at = _try_ns(rule.get("at_ns")) if "at_ns" in rule else None
            if at is not None and w.horizon_ns is not None and at > w.horizon_ns:
                out.append(Issue(ERROR, _child(rp, "at_ns"),
                                 f"decision time {_fmt_ns(at)} is outside the horizon ({_fmt_ns(w.horizon_ns)}); "
                                 "the rule would never fire"))
            elif at is not None and at < w.epoch_ns:
                out.append(Issue(WARNING, _child(rp, "at_ns"),
                                 f"decision time {_fmt_ns(at)} is before the clock epoch ({_fmt_ns(w.epoch_ns)})"))
            elif at is not None and w.horizon_ns is not None and at == w.horizon_ns:
                out.append(Issue(WARNING, _child(rp, "at_ns"),
                                 "decision time equals the horizon; events received at the very end may be cut off"))
            when = rule.get("when")
            if isinstance(when, str) and when.strip():
                wp = _child(rp, "when")
                issues = check_predicate(when, wp, w)
                out.extend(issues)
                if not any(x.severity == ERROR for x in issues):
                    pred = parse_predicate(when)
                    if at is not None and w.injects:
                        win = _try_ns(rule.get("window_ns"))
                        lo = at - (win if win is not None else at)
                        dep = {k for k in pred.kinds if k in w.kinds} - {"correction"}
                        in_range = [inj for _, inj, _ in w.injects if inj.get("kind") in dep
                                    and (_try_ns(inj.get("at_ns")) or 0) <= at
                                    and (_try_ns(inj.get("at_ns")) or 0) >= lo - 0]
                        if dep and not in_range:
                            out.append(Issue(WARNING, wp, f"no inject of kind {', '.join(sorted(dep))} happens "
                                                          f"inside the decision window [{_fmt_ns(max(lo, 0))}, "
                                                          f"{_fmt_ns(at)}]; the rule always sees nothing"))
                    if role is not None:
                        for k in sorted(pred.kinds - {"correction"}):
                            evs = [inj for _, inj, _ in w.injects if inj.get("kind") == k]
                            if evs and not any(_visible(role, inj.get("policy_labels") or []) for inj in evs):
                                out.append(Issue(WARNING, wp, f"actor '{a.get('id')}' counts kind '{k}' but its role "
                                                              f"'{role_id}' cannot see any '{k}' inject "
                                                              f"(visibility {role.get('visibility')})"))
            if rule.get("then") is not None and rule.get("then") == rule.get("otherwise"):
                out.append(Issue(WARNING, _child(rp, "otherwise"),
                                 "then and otherwise are the same option; the decision can never differ"))
            win = _try_ns(rule.get("window_ns")) if "window_ns" in rule else None
            if win == 0:
                out.append(Issue(WARNING, _child(rp, "window_ns"),
                                 "window_ns is 0: only events received exactly at the decision instant count"))
    dupes(rule_ids, "decision rule")
    if not n_rules:
        out.append(Issue(WARNING, "actors", "no decision rules: nothing decides, so decision.* invariants "
                                            "will be UNKNOWN"))

    # ---- injects
    injects = data.get("injects") or []
    dupes([(_child(_child("injects", i), "id"), eid) for i, _, eid in w.injects], "inject")
    by_id = {eid: (i, inj) for i, inj, eid in w.injects}
    for i, inj, eid in w.injects:
        ip = _child("injects", i)
        at = _try_ns(inj.get("at_ns"))
        if at is not None and w.horizon_ns is not None and at > w.horizon_ns:
            out.append(Issue(WARNING, _child(ip, "at_ns"), f"inject '{eid}' at {_fmt_ns(at)} is after the horizon "
                                                           f"({_fmt_ns(w.horizon_ns)}) and never happens"))
        payload = inj.get("payload") if isinstance(inj.get("payload"), dict) else {}
        for ref_key in ("corrects", "acknowledges"):
            if ref_key not in payload:
                continue
            target = payload[ref_key]
            pp = _child(_child(ip, "payload"), ref_key)
            if str(target) not in by_id:
                out.append(Issue(ERROR, pp, f"{ref_key} '{target}' is not an inject id"
                                            f"{_did_you_mean(str(target), w.inject_ids)}"))
                continue
            t_at = _try_ns(by_id[str(target)][1].get("at_ns"))
            if at is not None and t_at is not None and t_at > at:
                out.append(Issue(WARNING, pp, f"'{eid}' {ref_key} '{target}', which happens later "
                                              f"({_fmt_ns(t_at)} > {_fmt_ns(at)})"))
            if str(target) == eid:
                out.append(Issue(ERROR, pp, f"'{eid}' {ref_key} itself"))
        if inj.get("kind") == "correction" and "corrects" not in payload:
            out.append(Issue(WARNING, _child(ip, "payload"), f"correction '{eid}' has no payload.corrects; "
                                                             "the not_corrected/corrected flags will ignore it"))
    if isinstance(injects, list) and not injects:
        out.append(Issue(WARNING, "injects", "no injects: the world is empty"))

    # ---- transitions
    trans = data.get("transitions") or []
    trans = trans if isinstance(trans, list) else []
    dupes([(_child(_child("transitions", i), "id"), t["id"]) for i, t in enumerate(trans)
           if isinstance(t, dict) and isinstance(t.get("id"), str)], "transition")
    for i, t in enumerate(trans):
        if not isinstance(t, dict):
            continue
        tp = _child("transitions", i)
        lo, hi = _try_ns(t.get("from_ns")), _try_ns(t.get("to_ns"))
        if lo is not None and hi is not None and hi < lo:
            out.append(Issue(ERROR, _child(tp, "to_ns"), f"to_ns ({_fmt_ns(hi)}) is before from_ns ({_fmt_ns(lo)})"))
        if lo is not None and w.horizon_ns is not None and lo > w.horizon_ns:
            out.append(Issue(WARNING, _child(tp, "from_ns"), "transition starts after the horizon and never happens"))
        for j, rid in enumerate(t.get("roles") or [] if isinstance(t.get("roles"), list) else []):
            if isinstance(rid, str) and rid not in w.role_ids:
                out.append(Issue(ERROR, _child(_child(tp, "roles"), j),
                                 f"unknown role '{rid}'{_did_you_mean(rid, w.role_ids)}"))
        src = t.get("source_id")
        if isinstance(src, str) and w.sources and src not in w.sources:
            out.append(Issue(WARNING, _child(tp, "source_id"), f"no inject comes from source '{src}'"
                                                               f"{_did_you_mean(src, w.sources)}; the outage "
                                                               "silences nothing"))
        elif isinstance(src, str) and lo is not None and hi is not None and not any(
                inj.get("source_id") == src and lo <= (_try_ns(inj.get("at_ns")) or -1) <= hi
                for _, inj, _ in w.injects):
            out.append(Issue(WARNING, tp, f"no inject from '{src}' falls inside [{_fmt_ns(lo)}, {_fmt_ns(hi)}]; "
                                          "the outage silences nothing"))

    # ---- invariants
    if invariant_ids is not None:
        for i, inv_id in enumerate(data.get("invariants") or [] if isinstance(data.get("invariants"), list) else []):
            if isinstance(inv_id, str) and inv_id not in invariant_ids:
                out.append(Issue(ERROR, _child("invariants", i), f"unknown invariant '{inv_id}'"
                                                                 f"{_did_you_mean(inv_id, invariant_ids)}"))
    return out


# ============================================================================ entry points

def _invariant_ids(invariants: str | Path | Iterable[str] | None) -> tuple[set[str] | None, list[Issue]]:
    if invariants is None:
        return None, []
    if isinstance(invariants, (str, Path)):
        from worldline.invariants import load_invariants  # local import: keep worlds free of a hard dependency
        p = Path(invariants)
        if not p.exists():
            return None, [Issue(ERROR, "invariants", f"invariants path '{p}' does not exist")]
        try:
            return set(load_invariants(p)), []
        except Exception as exc:  # noqa: BLE001 - any loader failure is a user-facing error
            return None, [Issue(ERROR, "invariants", f"could not load invariants from '{p}': {exc}")]
    return set(invariants), []


def validate_data(data: Any, *, invariants: str | Path | Iterable[str] | None = None,
                  text: str | None = None, source: str | None = None) -> ValidationReport:
    """Validate a parsed scenario document.

    ``invariants``: a directory/file of invariant declarations, or an iterable of ids;
    ``None`` skips the invariant-reference check. ``text`` (the YAML source) enables
    line numbers and duplicate-key detection.
    """
    issues: list[Issue] = []
    lines: dict[str, int] = {}
    if text is not None:
        lines, dup = _source_map(text)
        issues.extend(dup)
    if not isinstance(data, dict):
        issues.append(Issue(ERROR, "", "a scenario must be a YAML mapping with at least id, seed and clock"))
        return ValidationReport(source, issues)
    issues.extend(validate_structure(data))
    inv_ids, inv_issues = _invariant_ids(invariants)
    issues.extend(inv_issues)
    issues.extend(_semantic(data, inv_ids))
    if not any(i.severity == ERROR for i in issues):
        try:  # safety net: the real loader must agree
            scenario_from_dict(data)
        except Exception as exc:  # noqa: BLE001
            issues.append(Issue(ERROR, "", f"the scenario loader rejected the document: {exc}"))
    if text is not None and data.get("specimen") is True and "SPECIMEN:" not in text:
        issues.append(Issue(WARNING, "specimen", "specimen: true without a '# SPECIMEN: ...' header comment "
                                                 "explaining the seeded defect (see scenarios/port_alert.yaml)"))
    issues = [Issue(i.severity, i.path, i.message, i.line if i.line is not None else _line_for(i.path, lines))
              for i in issues]
    issues.sort(key=lambda i: (i.severity != ERROR, i.line if i.line is not None else 0, i.path))
    summary = {
        "id": data.get("id"),
        "roles": len(data.get("roles") or []) if isinstance(data.get("roles"), list) else 0,
        "actors": len(data.get("actors") or []) if isinstance(data.get("actors"), list) else 0,
        "decision_rules": sum(len(a.get("decision_rules") or []) for a in data.get("actors") or []
                              if isinstance(a, dict) and isinstance(a.get("decision_rules"), list))
        if isinstance(data.get("actors"), list) else 0,
        "injects": len(data.get("injects") or []) if isinstance(data.get("injects"), list) else 0,
        "transitions": len(data.get("transitions") or []) if isinstance(data.get("transitions"), list) else 0,
    }
    return ValidationReport(source, issues, summary)


def validate_text(text: str, *, invariants: str | Path | Iterable[str] | None = None,
                  source: str | None = None) -> ValidationReport:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark is not None else None
        problem = getattr(exc, "problem", None) or str(exc)
        return ValidationReport(source, [Issue(ERROR, "", f"YAML syntax error: {problem}", line)])
    return validate_data(data, invariants=invariants, text=text, source=source)


def validate_file(path: str | Path, *, invariants: str | Path | Iterable[str] | None = None) -> ValidationReport:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        return ValidationReport(str(p), [Issue(ERROR, "", f"cannot read file: {exc}")])
    return validate_text(text, invariants=invariants, source=str(p))


# ============================================================================ examples and templates

EXAMPLE_SCENARIO = """\
# Minimal valid WORLDLINE scenario: one role, one actor, one rule, one correctable report.
id: minimal_example
seed: 1
clock: {epoch_ns: 0, tick_ns: 1s, horizon_ns: 10m}
roles:
  - id: operator
    visibility: ["*"]
    delivery: {base_latency_ns: 5s, jitter_ns: 2s, drop_probability: 0.0, ordering: fifo}
actors:
  - id: shift_lead
    role: operator
    decision_rules:
      - id: act_or_wait
        at_ns: 300s
        window_ns: 300s
        when: "count(kind='report', payload.severity>=3, not_corrected) >= 1"
        then: act
        otherwise: wait
injects:
  - {id: report-001, at_ns: 60s, source_id: sensor_a, kind: report, payload: {severity: 4}}
  - {id: correction-001, at_ns: 200s, source_id: sensor_a, kind: correction,
     payload: {corrects: report-001, severity: 1, reason: "calibration fault"}}
invariants: [epistemic.no_future_knowledge, decision.consistent_with_truth]
"""


def scaffold(scenario_id: str = "my_scenario") -> str:
    """A fully commented scenario template that validates as-is (used by ``worldline init``)."""
    return TEMPLATE.replace("__ID__", scenario_id)


TEMPLATE = """\
# WORLDLINE scenario: __ID__
#
# Describe the world in two or three sentences: who observes what, and which decision matters.
# Every *_ns field takes integer nanoseconds or a string with a unit: ns, us, ms, s, m, h.
# Validate as you edit:   uv run worldline validate <this file>
# Then run it:            uv run worldline run <this file>
# Reference:              docs/AUTHORING.md
#
# If this scenario deliberately seeds a defect for the explorer to find, add the two lines below
# (a '# SPECIMEN:' header comment plus 'specimen: true') and describe the defect above them.
# SPECIMEN: deliberately defective laboratory scenario. Frozen. Repairs land on scenarios/product/.
# specimen: true

id: __ID__            # letters, digits, '_', '.', '-'; appears in every trace and genome
seed: 1                # drives delivery jitter and drops; same seed => byte-identical trace
clock:
  epoch_ns: 0
  tick_ns: 1s
  horizon_ns: 30m      # simulated time ends here; decisions must happen before it

# Roles: who receives what, how late, and which policy labels they may see.
roles:
  - id: front_desk
    visibility: ["*"]                     # "*" sees everything; otherwise list labels
    delivery: {base_latency_ns: 2s, jitter_ns: 1s, drop_probability: 0.0, ordering: fifo}
  - id: back_office
    visibility: [shared]                  # sees only events whose labels are ALL in this list
    delivery: {base_latency_ns: 30s, jitter_ns: 15s, drop_probability: 0.0, ordering: network}
  - id: evaluator                         # convention: an omniscient, zero-latency observer
    visibility: ["*"]
    delivery: {base_latency_ns: 0, jitter_ns: 0, drop_probability: 0.0, ordering: fifo}

# Actors decide with deterministic rules over what their role KNOWS at at_ns (never truth).
actors:
  - id: supervisor
    role: back_office
    decision_rules:
      - id: escalate_or_hold
        at_ns: 600s                       # decision point
        window_ns: 600s                   # counts events received in [at_ns - window_ns, at_ns]
        when: "count(kind='report', payload.severity>=3, not_corrected) >= 1"
        then: escalate
        otherwise: hold

# Injects: the truth. What actually happens, and when.
injects:
  - {id: report-001, at_ns: 120s, source_id: field_unit, kind: report,
     payload: {severity: 4, note: "first report"}, policy_labels: [shared]}
  # A correction names the event it corrects in payload.corrects; not_corrected then drops it.
  - {id: correction-001, at_ns: 400s, source_id: field_unit, kind: correction,
     payload: {corrects: report-001, severity: 1, reason: "duplicate of an old ticket"}, policy_labels: [shared]}
  - {id: status-001, at_ns: 900s, source_id: field_unit, kind: status,
     payload: {state: closed}, policy_labels: [shared]}

# Transitions: changes of the world itself. kind outage silences a source for [from_ns, to_ns].
transitions:
  - {id: radio_outage, kind: outage, from_ns: 850s, to_ns: 950s, source_id: field_unit}

# Invariants checked by default (ids declared in invariants/*.yaml).
invariants:
  - epistemic.no_future_knowledge
  - decision.consistent_with_truth
  - absence.no_corrupted_in_decision
"""
