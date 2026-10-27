"""Translate between Event v1 and the example service's HTTP API, in both directions.

Outbound (``to_request``): a truth event (possibly perturbed by a FaultSchedule) becomes one
HTTP request. Which endpoint is a routing table keyed by event kind; kinds without a route go to
the generic ``/observations`` endpoint so the service's receive log covers every delivery.

Inbound (``view_from_log``): the service's own receive log (``GET /log``) becomes the role view
EventLog. The view is rebuilt from what the SERVICE says it accepted and when, not from what the
driver intended to send: a request the service rejected (e.g. a corrupted confidence of -0.8
failing validation) is absent from the view, exactly as it is absent from the service's state.
"""
from __future__ import annotations

from typing import Any

from worldline.core import Event, EventLog

# kind -> (path, channel). The channel is the service's record type.
DEFAULT_ROUTES: dict[str, tuple[str, str]] = {
    "sighting": ("/reports", "report"),
    "correction": ("/corrections", "correction"),
}
FALLBACK_ROUTE = ("/observations", "observation")


def _meta(ev: Event) -> dict:
    return {"quality": ev.quality, "provenance_ids": list(ev.provenance_ids),
            "policy_labels": list(ev.policy_labels), "priority": ev.priority}


def to_request(ev: Event, routes: dict[str, tuple[str, str]] = DEFAULT_ROUTES) -> tuple[str, dict]:
    """Event -> (path, json body) for the example service."""
    path, channel = routes.get(ev.kind, FALLBACK_ROUTE)
    p = dict(ev.payload)
    if channel == "report":
        body: dict[str, Any] = {"id": ev.id, "source_id": ev.source_id, "observed_at_ns": ev.event_time_ns,
                                "vessel": p.pop("vessel", None), "confidence": p.pop("confidence", None),
                                "details": p, "meta": _meta(ev)}
    elif channel == "correction":
        body = {"id": ev.id, "source_id": ev.source_id, "observed_at_ns": ev.event_time_ns,
                "corrects": p.pop("corrects", None), "confidence": p.pop("confidence", None),
                "reason": p.pop("reason", None), "details": p, "meta": _meta(ev)}
    else:
        body = {"id": ev.id, "kind": ev.kind, "source_id": ev.source_id, "observed_at_ns": ev.event_time_ns,
                "payload": p, "meta": _meta(ev)}
    return path, body


def _channel_kinds(routes: dict[str, tuple[str, str]]) -> dict[str, str]:
    return {channel: kind for kind, (_, channel) in routes.items()}


def record_to_event(rec: dict, scenario_id: str, routes: dict[str, tuple[str, str]] = DEFAULT_ROUTES) -> Event:
    """One service log record -> Event v1 with received_time_ns = the service's receipt stamp."""
    body = dict(rec["body"])
    meta = body.get("meta") or {}
    channel = rec["channel"]
    if channel == "report":
        payload = dict(body.get("details") or {})
        if body.get("vessel") is not None:
            payload["vessel"] = body["vessel"]
        payload["confidence"] = body["confidence"]
        kind = _channel_kinds(routes)["report"]
    elif channel == "correction":
        payload = dict(body.get("details") or {})
        payload["corrects"] = body["corrects"]
        for k in ("confidence", "reason"):
            if body.get(k) is not None:
                payload[k] = body[k]
        kind = _channel_kinds(routes)["correction"]
    else:
        payload = dict(body.get("payload") or {})
        kind = body["kind"]
    return Event(
        id=rec["id"], scenario_id=scenario_id, source_id=rec["source_id"],
        event_time_ns=int(rec["observed_at_ns"]), kind=kind, payload=payload,
        received_time_ns=int(rec["received_at_ns"]), quality=meta.get("quality"),
        policy_labels=tuple(meta.get("policy_labels") or ()), provenance_ids=tuple(meta.get("provenance_ids") or ()),
        priority=int(meta.get("priority") or 0),
    )


def view_from_log(log_json: dict, scenario_id: str, routes: dict[str, tuple[str, str]] = DEFAULT_ROUTES) -> EventLog:
    """The service's receive log -> view EventLog, appended in received order."""
    evs = [record_to_event(r, scenario_id, routes) for r in log_json["records"]]
    evs.sort(key=lambda e: (e.received_time_ns, *e.sort_key()))
    log = EventLog()
    for e in evs:
        log.append(e)
    return log
