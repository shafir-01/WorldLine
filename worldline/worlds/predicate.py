"""A tiny, safe predicate DSL for decision rules. No ``eval``; a regex tokenizer
and a recursive-descent parser over a closed grammar.

Grammar::

    expr        := and_expr ('or' and_expr)*
    and_expr    := not_expr ('and' not_expr)*
    not_expr    := 'not' not_expr | primary
    primary     := '(' expr ')' | call (CMP NUMBER)?
    call        := NAME '(' [arg (',' arg)*] ')'
    arg         := FIELD ('=' | CMP) LITERAL      # filter, e.g. kind='sighting', payload.confidence>=0.6
                 | NAME                           # flag, e.g. not_corrected
    CMP         := '>=' | '<=' | '==' | '!=' | '>' | '<'
    LITERAL     := 'string' | number | duration | true | false | null
    duration    := number followed by ns|us|ms|s|m|h, e.g. 600s (converted to integer ns)

Functions (evaluated over the events an actor *knows*, see ``Context``):

    count(filters...)               number of windowed known events matching all filters
    exists(filters...)              count(...) > 0
    acknowledged(kind='alert', within_ns=N, ack_kind='ack')
                                    true iff at least one windowed event of ``kind`` is known and
                                    every such event A has a known event of ``ack_kind`` whose
                                    payload.acknowledges == A.id and 0 <= ack.event_time - A.event_time <= N

Flags:
    not_corrected   no known event of kind "correction" has payload.corrects == this event's id
    corrected       the opposite

Field paths: id, kind, source_id, event_time_ns, received_time_ns, priority, quality,
unit, and ``payload.a.b`` for nested payload fields. A comparison against a
missing field or a mismatched type is False (never an exception).

A bare call used as a boolean is truthy when non-zero / True.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from worldline.core import Event

_TOKEN = re.compile(
    r"\s*(?:"
    r"(?P<dur>-?\d+(?:\.\d+)?(?:ns|us|ms|s|m|h)(?![A-Za-z0-9_]))"
    r"|(?P<num>-?\d+(?:\.\d+)?)"
    r"|(?P<str>'[^']*'|\"[^\"]*\")"
    r"|(?P<op>>=|<=|==|!=|>|<|=)"
    r"|(?P<punc>[(),])"
    r"|(?P<name>[A-Za-z_][A-Za-z0-9_.]*)"
    r")"
)

_UNITS = {"ns": 1, "us": 1_000, "ms": 1_000_000, "s": 1_000_000_000, "m": 60_000_000_000, "h": 3_600_000_000_000}

_CMP: dict[str, Callable[[Any, Any], bool]] = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "=": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
}

_KEYWORDS = {"and", "or", "not", "true", "false", "null"}
_FUNCTIONS = {"count", "exists", "acknowledged"}
_FLAGS = {"not_corrected", "corrected"}
_EVENT_FIELDS = {"id", "kind", "source_id", "event_time_ns", "received_time_ns", "priority", "quality", "unit"}


class PredicateError(ValueError):
    pass


@dataclass(frozen=True)
class Token:
    type: str
    value: Any
    pos: int


def tokenize(text: str) -> list[Token]:
    out: list[Token] = []
    pos = 0
    while pos < len(text):
        if text[pos:].strip() == "":
            break
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise PredicateError(f"unexpected character at {pos}: {text[pos:pos + 10]!r}")
        pos = m.end()
        kind = m.lastgroup
        raw = m.group(kind)
        if kind == "num":
            out.append(Token("num", float(raw) if "." in raw else int(raw), m.start(kind)))
        elif kind == "dur":
            qty, unit = re.fullmatch(r"(-?\d+(?:\.\d+)?)(ns|us|ms|s|m|h)", raw).groups()
            out.append(Token("num", int(round(float(qty) * _UNITS[unit])), m.start(kind)))
        elif kind == "str":
            out.append(Token("str", raw[1:-1], m.start(kind)))
        elif kind == "name":
            low = raw.lower()
            if low in _KEYWORDS:
                out.append(Token("kw", low, m.start(kind)))
            else:
                out.append(Token("name", raw, m.start(kind)))
        else:
            out.append(Token(kind, raw, m.start(kind)))
    return out


# --- AST ---------------------------------------------------------------------

@dataclass(frozen=True)
class Filter:
    field: str
    op: str
    value: Any


@dataclass(frozen=True)
class Call:
    name: str
    filters: tuple[Filter, ...] = ()
    flags: tuple[str, ...] = ()
    kwargs: dict = field(default_factory=dict)   # non-filter arguments, e.g. within_ns


@dataclass(frozen=True)
class Compare:
    call: Call
    op: str
    value: Any


@dataclass(frozen=True)
class Not:
    inner: Any


@dataclass(frozen=True)
class BoolOp:
    op: str      # "and" | "or"
    items: tuple


class _Parser:
    def __init__(self, tokens: list[Token], text: str):
        self.toks = tokens
        self.i = 0
        self.text = text

    def peek(self, type_: str | None = None, value: Any = None) -> Token | None:
        if self.i >= len(self.toks):
            return None
        t = self.toks[self.i]
        if type_ is not None and t.type != type_:
            return None
        if value is not None and t.value != value:
            return None
        return t

    def take(self, type_: str | None = None, value: Any = None) -> Token:
        t = self.peek(type_, value)
        if t is None:
            got = self.toks[self.i] if self.i < len(self.toks) else "end of input"
            raise PredicateError(f"expected {value or type_} but got {got} in {self.text!r}")
        self.i += 1
        return t

    def parse(self):
        node = self.expr()
        if self.i != len(self.toks):
            raise PredicateError(f"trailing tokens after position {self.toks[self.i].pos} in {self.text!r}")
        return node

    def expr(self):
        items = [self.and_expr()]
        while self.peek("kw", "or"):
            self.take()
            items.append(self.and_expr())
        return items[0] if len(items) == 1 else BoolOp("or", tuple(items))

    def and_expr(self):
        items = [self.not_expr()]
        while self.peek("kw", "and"):
            self.take()
            items.append(self.not_expr())
        return items[0] if len(items) == 1 else BoolOp("and", tuple(items))

    def not_expr(self):
        if self.peek("kw", "not"):
            self.take()
            return Not(self.not_expr())
        return self.primary()

    def primary(self):
        if self.peek("punc", "("):
            self.take()
            node = self.expr()
            self.take("punc", ")")
            return node
        call = self.call()
        if self.peek("op"):
            op = self.take("op").value
            if op == "=":
                op = "=="
            num = self.take("num").value
            return Compare(call, op, num)
        return call

    def call(self) -> Call:
        name_tok = self.take("name")
        name = name_tok.value
        if name not in _FUNCTIONS:
            raise PredicateError(f"unknown function {name!r}; allowed: {sorted(_FUNCTIONS)}")
        self.take("punc", "(")
        filters: list[Filter] = []
        flags: list[str] = []
        kwargs: dict = {}
        if not self.peek("punc", ")"):
            while True:
                self.arg(name, filters, flags, kwargs)
                if self.peek("punc", ","):
                    self.take()
                    continue
                break
        self.take("punc", ")")
        return Call(name, tuple(filters), tuple(flags), kwargs)

    def arg(self, fn: str, filters: list[Filter], flags: list[str], kwargs: dict) -> None:
        tok = self.take("name")
        ident = tok.value
        if not self.peek("op"):
            if ident not in _FLAGS:
                raise PredicateError(f"unknown flag {ident!r}; allowed: {sorted(_FLAGS)}")
            flags.append(ident)
            return
        op = self.take("op").value
        lit = self.literal()
        if fn == "acknowledged" and ident in ("within_ns", "ack_kind"):
            if op not in ("=", "=="):
                raise PredicateError(f"{ident} takes '=', not {op!r}")
            kwargs[ident] = lit
            return
        if not (ident in _EVENT_FIELDS or ident.startswith("payload.")):
            raise PredicateError(f"unknown field {ident!r}")
        filters.append(Filter(ident, "==" if op == "=" else op, lit))

    def literal(self) -> Any:
        t = self.peek()
        if t is None:
            raise PredicateError(f"expected a literal at end of {self.text!r}")
        if t.type in ("num", "str"):
            self.i += 1
            return t.value
        if t.type == "kw" and t.value in ("true", "false", "null"):
            self.i += 1
            return {"true": True, "false": False, "null": None}[t.value]
        raise PredicateError(f"expected a literal but got {t} in {self.text!r}")


# --- evaluation --------------------------------------------------------------

def field_value(ev: Event, path: str) -> Any:
    if path.startswith("payload."):
        cur: Any = ev.payload
        for part in path.split(".")[1:]:
            if not isinstance(cur, dict) or part not in cur:
                return None
            cur = cur[part]
        return cur
    return getattr(ev, path, None)


def _compare(a: Any, op: str, b: Any) -> bool:
    if op in ("==", "!="):
        if isinstance(a, bool) != isinstance(b, bool) and (a is not None and b is not None):
            # bool vs number: compare as different unless both bool
            if isinstance(a, bool) or isinstance(b, bool):
                return op == "!="
        return _CMP[op](a, b)
    if a is None or b is None or isinstance(a, bool) or isinstance(b, bool):
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return _CMP[op](a, b)
    if isinstance(a, str) and isinstance(b, str):
        return _CMP[op](a, b)
    return False


@dataclass
class Context:
    """What an actor knows at decision time.

    known:    every event received by the actor's role at or before at_ns
    windowed: the subset received within [at_ns - window_ns, at_ns]

    count/exists/acknowledged iterate ``windowed``; cross-references (corrections,
    acknowledgements) are looked up in ``known`` because knowledge does not expire
    just because it is old.
    """

    known: Sequence[Event]
    windowed: Sequence[Event]

    def corrected_ids(self) -> set[str]:
        return {
            str(e.payload.get("corrects"))
            for e in self.known
            if e.kind == "correction" and e.payload.get("corrects") is not None
        }


def _matches(ev: Event, call: Call, ctx: Context, corrected: set[str]) -> bool:
    for f in call.filters:
        if not _compare(field_value(ev, f.field), f.op, f.value):
            return False
    for flag in call.flags:
        if flag == "not_corrected" and ev.id in corrected:
            return False
        if flag == "corrected" and ev.id not in corrected:
            return False
    return True


def _select(call: Call, ctx: Context) -> list[Event]:
    corrected = ctx.corrected_ids() if call.flags else set()
    return [e for e in ctx.windowed if _matches(e, call, ctx, corrected)]


def _eval_call(call: Call, ctx: Context) -> Any:
    if call.name == "count":
        return len(_select(call, ctx))
    if call.name == "exists":
        return len(_select(call, ctx)) > 0
    if call.name == "acknowledged":
        targets = _select(call, ctx)
        if not targets:
            return False
        ack_kind = call.kwargs.get("ack_kind", "ack")
        within = call.kwargs.get("within_ns")
        acks = [e for e in ctx.known if e.kind == ack_kind]
        for a in targets:
            ok = False
            for ack in acks:
                if str(ack.payload.get("acknowledges")) != a.id:
                    continue
                dt = ack.event_time_ns - a.event_time_ns
                if dt < 0:
                    continue
                if within is not None and dt > within:
                    continue
                ok = True
                break
            if not ok:
                return False
        return True
    raise PredicateError(f"unknown function {call.name!r}")


def evaluate(node: Any, ctx: Context) -> bool:
    if isinstance(node, Compare):
        return bool(_compare(_eval_call(node.call, ctx), node.op, node.value))
    if isinstance(node, Call):
        return bool(_eval_call(node, ctx))
    if isinstance(node, Not):
        return not evaluate(node.inner, ctx)
    if isinstance(node, BoolOp):
        if node.op == "and":
            return all(evaluate(i, ctx) for i in node.items)
        return any(evaluate(i, ctx) for i in node.items)
    raise PredicateError(f"bad node {node!r}")


def referenced_kinds(node: Any) -> set[str]:
    """Event kinds a predicate depends on (used by guided exploration, read from the Scenario)."""
    out: set[str] = set()
    if isinstance(node, Compare):
        node = node.call
    if isinstance(node, Call):
        for f in node.filters:
            if f.field == "kind" and f.op == "==" and isinstance(f.value, str):
                out.add(f.value)
        if "not_corrected" in node.flags or "corrected" in node.flags:
            out.add("correction")
        if node.name == "acknowledged":
            out.add(str(node.kwargs.get("ack_kind", "ack")))
    elif isinstance(node, Not):
        out |= referenced_kinds(node.inner)
    elif isinstance(node, BoolOp):
        for i in node.items:
            out |= referenced_kinds(i)
    return out


@dataclass(frozen=True)
class Predicate:
    text: str
    ast: Any

    def evaluate(self, known: Sequence[Event], windowed: Sequence[Event] | None = None) -> bool:
        return evaluate(self.ast, Context(known=known, windowed=known if windowed is None else windowed))

    @property
    def kinds(self) -> set[str]:
        return referenced_kinds(self.ast)


def parse_predicate(text: str) -> Predicate:
    if not isinstance(text, str) or not text.strip():
        raise PredicateError("empty predicate")
    return Predicate(text, _Parser(tokenize(text), text).parse())
