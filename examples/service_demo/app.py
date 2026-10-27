"""Approvals API: a small FastAPI service that decides whether to escalate an incident.

This is ordinary request-handling code. It knows nothing about WORLDLINE's Event v1, traces,
or invariants. It only:

* accepts field reports (``POST /reports``), corrections of earlier reports
  (``POST /corrections``) and context observations (``POST /observations``);
* keeps, per client, an in-memory receive log stamped with the time each request arrived;
* answers ``GET /decision?at=<t>`` from the records it had RECEIVED by ``t``.

The decision rule is the port_alert duty-officer rule written as plain Python:
escalate when at least two credible (confidence >= 0.6), uncorrected reports arrived inside
the last 600 s, otherwise monitor.

Virtual time: every write and every decision carries ``X-Worldline-Now`` (integer
nanoseconds). The service uses it as "now" instead of the wall clock, so a run is
deterministic and replayable. A client clock may not go backwards (409).
``X-Worldline-Client`` partitions state (default "default"); ``POST /reset`` clears one
client's state. No database; everything lives in process memory.

Run it standalone:  uvicorn examples.service_demo.app:app --port 8100
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

NOW_HEADER = "X-Worldline-Now"
CLIENT_HEADER = "X-Worldline-Client"
S = 1_000_000_000

# The rule, as the service team would configure it.
RULE_ID = "escalate_or_monitor"
WINDOW_NS = 600 * S
MIN_CONFIDENCE = 0.6
THRESHOLD = 2


# ------------------------------------------------------------------ request bodies

class Meta(BaseModel):
    """Opaque transport metadata the service stores verbatim (quality marks, provenance)."""
    quality: float | None = None
    provenance_ids: list[str] = Field(default_factory=list)
    policy_labels: list[str] = Field(default_factory=list)
    priority: int = 0


class ReportIn(BaseModel):
    id: str = Field(min_length=1)
    source_id: str
    observed_at_ns: int = Field(ge=0)
    vessel: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    details: dict[str, Any] = Field(default_factory=dict)
    meta: Meta = Field(default_factory=Meta)


class CorrectionIn(BaseModel):
    id: str = Field(min_length=1)
    source_id: str
    observed_at_ns: int = Field(ge=0)
    corrects: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    reason: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    meta: Meta = Field(default_factory=Meta)


class ObservationIn(BaseModel):
    id: str = Field(min_length=1)
    kind: str
    source_id: str
    observed_at_ns: int = Field(ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)
    meta: Meta = Field(default_factory=Meta)


# ------------------------------------------------------------------ state

@dataclass
class Record:
    id: str
    channel: str                 # "report" | "correction" | "observation"
    received_at_ns: int
    observed_at_ns: int
    source_id: str
    confidence: float | None = None
    corrects: str | None = None
    body: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ClientState:
    now_ns: int = 0
    log: list[Record] = field(default_factory=list)
    ids: set[str] = field(default_factory=set)


# ------------------------------------------------------------------ the rule (pure; callable directly)

def escalation_decision(records: Iterable[Record], at_ns: int, *, window_ns: int = WINDOW_NS,
                        min_confidence: float = MIN_CONFIDENCE, threshold: int = THRESHOLD) -> dict:
    """Decide from the records received by ``at_ns``.

    Corrections are honoured whenever they were received (knowledge does not expire); only
    reports received inside the window count.
    """
    known = [r for r in records if r.received_at_ns <= at_ns]
    corrected = {r.corrects for r in known if r.channel == "correction" and r.corrects is not None}
    credible = [r for r in known
                if r.channel == "report" and r.received_at_ns >= at_ns - window_ns
                and r.confidence is not None and r.confidence >= min_confidence and r.id not in corrected]
    escalate = len(credible) >= threshold
    return {
        "rule_id": RULE_ID,
        "at_ns": at_ns,
        "option": "escalate" if escalate else "monitor",
        "predicate_value": escalate,
        "credible_report_ids": [r.id for r in credible],
        "known_event_ids": [r.id for r in sorted(known, key=lambda r: (r.received_at_ns, r.id))],
    }


# ------------------------------------------------------------------ app

def create_app() -> FastAPI:
    api = FastAPI(title="approvals-demo", version="0.1.0",
                  description="Example service: incident escalation approvals over field reports.")
    clients: dict[str, ClientState] = {}

    def state(client: str) -> ClientState:
        return clients.setdefault(client, ClientState())

    def tick(st: ClientState, now: int | None) -> int:
        if now is None:
            raise HTTPException(400, f"missing {NOW_HEADER} header (virtual time, integer ns)")
        if now < st.now_ns:
            raise HTTPException(409, f"virtual clock went backwards: {now} < {st.now_ns}")
        st.now_ns = now
        return now

    def ingest(st: ClientState, now: int, rec: Record) -> dict:
        if rec.id in st.ids:  # idempotent: a retried request is acknowledged, not re-logged
            return {"id": rec.id, "status": "duplicate", "received_at_ns": now}
        st.ids.add(rec.id)
        st.log.append(rec)
        return {"id": rec.id, "status": "accepted", "received_at_ns": now}

    @api.post("/reset")
    def reset(x_worldline_client: str = Header("default")) -> dict:
        clients.pop(x_worldline_client, None)
        return {"reset": x_worldline_client}

    @api.post("/reports", status_code=201)
    def post_report(body: ReportIn, x_worldline_now: int | None = Header(None),
                    x_worldline_client: str = Header("default")) -> dict:
        st = state(x_worldline_client)
        now = tick(st, x_worldline_now)
        return ingest(st, now, Record(body.id, "report", now, body.observed_at_ns, body.source_id,
                                      confidence=body.confidence, body=body.model_dump()))

    @api.post("/corrections", status_code=201)
    def post_correction(body: CorrectionIn, x_worldline_now: int | None = Header(None),
                        x_worldline_client: str = Header("default")) -> dict:
        st = state(x_worldline_client)
        now = tick(st, x_worldline_now)
        return ingest(st, now, Record(body.id, "correction", now, body.observed_at_ns, body.source_id,
                                      confidence=body.confidence, corrects=body.corrects, body=body.model_dump()))

    @api.post("/observations", status_code=201)
    def post_observation(body: ObservationIn, x_worldline_now: int | None = Header(None),
                         x_worldline_client: str = Header("default")) -> dict:
        st = state(x_worldline_client)
        now = tick(st, x_worldline_now)
        return ingest(st, now, Record(body.id, "observation", now, body.observed_at_ns, body.source_id,
                                      body=body.model_dump()))

    @api.get("/decision")
    def get_decision(at: int | None = Query(None, ge=0, description="decision time (ns); default: now"),
                     x_worldline_now: int | None = Header(None),
                     x_worldline_client: str = Header("default")) -> dict:
        st = state(x_worldline_client)
        now = tick(st, x_worldline_now)
        at_ns = now if at is None else at
        if at_ns > now:
            raise HTTPException(400, f"cannot decide in the future: at={at_ns} > now={now}")
        return escalation_decision(st.log, at_ns)

    @api.get("/log")
    def get_log(x_worldline_client: str = Header("default")) -> dict:
        st = state(x_worldline_client)
        return {"client": x_worldline_client, "now_ns": st.now_ns, "records": [r.to_dict() for r in st.log]}

    return api


app = create_app()
