"""USGS Earthquake Hazards Program adapter: real recorded events -> WORLDLINE truth stream.

Source: the FDSN event web service of the USGS ComCat catalogue
(https://earthquake.usgs.gov/fdsnws/event/1/), GeoJSON format, public domain, no key.

What this module treats as REAL (copied from the recorded USGS data, never invented):
  * which events happened in the window, their ids, places, locations, depths;
  * every published origin-product version with its ``updateTime`` (publication time),
    contributing network (``us``, ``at``, ``pt``, ...) and magnitude;
  * therefore the first-published magnitude of each event and each later revision.

What it treats as MODEL (declared parameters, labelled as such in every generated scenario):
  * the roles (``field_ops``, ``regional_hq``, ``evaluator``) and their delivery latency,
    jitter and ordering;
  * the actor ``duty_seismologist``, its decision rule, threshold and decision time.

Approximation (stated in the provenance notes): the magnitude "shown" at a given instant is
taken as the magnitude of the current origin product with the highest ``preferredWeight``
(ties: most recent update), which approximates ComCat's preferred-origin selection. A
``correction`` is emitted each time that shown magnitude changes inside the window.

Network I/O happens only in :func:`fetch_window`. :func:`to_scenario` works offline from a
committed snapshot and is deterministic: same snapshot + same parameters -> same bytes.
"""
from __future__ import annotations

import hashlib
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

FDSN_QUERY = "https://earthquake.usgs.gov/fdsnws/event/1/query"
LICENSE = "USGS, public domain"
USER_AGENT = "worldline-usgs-adapter/0.1 (research; https://earthquake.usgs.gov/fdsnws/event/1/)"
SNAPSHOT_SCHEMA = "worldline.usgs.snapshot/1"
NS_PER_MS = 1_000_000
NS_PER_S = 1_000_000_000

Fetcher = Callable[[str], bytes]


# ----------------------------------------------------------------------------- helpers

def _parse_utc(value: str) -> datetime:
    s = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ms_to_iso(ms: int) -> str:
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms % 1000:03d}Z"


def _epoch_ms(dt: datetime) -> int:
    return int(round(dt.timestamp() * 1000))


def canonical_bytes(obj: Any) -> bytes:
    """The byte string the snapshot hash is computed over (sorted keys, compact, UTF-8)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def window_key(start: str, end: str, minmag: float) -> str:
    """``2025-07-29T23:20:00`` .. -> ``20250729T2320Z_20250730T0140Z_m5.5``."""
    a, b = _parse_utc(start), _parse_utc(end)
    return f"{a:%Y%m%dT%H%MZ}_{b:%Y%m%dT%H%MZ}_m{float(minmag):g}"


def query_url(start: str, end: str, minmag: float) -> str:
    params = {"format": "geojson", "starttime": _iso(_parse_utc(start)), "endtime": _iso(_parse_utc(end)),
              "minmagnitude": f"{float(minmag):g}", "orderby": "time-asc"}
    return FDSN_QUERY + "?" + urllib.parse.urlencode(params)


def _http_get(url: str, timeout: float = 60.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https host)
        return resp.read()


def _origin_versions(detail: dict) -> list[dict]:
    """Every origin-product version (current and superseded) reduced to the fields we use."""
    out = []
    for o in (detail.get("properties", {}).get("products", {}) or {}).get("origin", []) or []:
        p = o.get("properties") or {}
        mag = p.get("magnitude")
        out.append({
            "source": o.get("source"), "code": o.get("code"), "status": o.get("status"),
            "update_time_ms": int(o.get("updateTime")), "preferred_weight": int(o.get("preferredWeight") or 0),
            "magnitude": float(mag) if mag not in (None, "") else None,
            "magnitude_type": p.get("magnitude-type"), "eventtime": p.get("eventtime"),
            "latitude": float(p["latitude"]) if p.get("latitude") not in (None, "") else None,
            "longitude": float(p["longitude"]) if p.get("longitude") not in (None, "") else None,
            "depth_km": float(p["depth"]) if p.get("depth") not in (None, "") else None,
            "review_status": p.get("review-status"),
        })
    out.sort(key=lambda v: (v["update_time_ms"], v["source"] or "", v["code"] or ""))
    return out


# ----------------------------------------------------------------------------- fetch + snapshot

def fetch_window(start: str, end: str, minmag: float, *, with_history: bool = True,
                 fetcher: Fetcher | None = None) -> dict:
    """Fetch one fixed historical window from the USGS FDSN event service.

    Returns the raw summary GeoJSON plus, per event, the origin-product history obtained from
    the event's ``detail`` URL with ``includesuperseded=true``. The detail documents are large
    (megabytes for a great earthquake: ShakeMap, PAGER, phase data ...); only the origin product
    versions are kept. The SHA-256 of every raw HTTP body is recorded so the trimming is auditable.
    """
    get = fetcher or _http_get
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = query_url(start, end, minmag)
    body = get(url)
    summary = json.loads(body)
    responses = [{"url": url, "sha256": sha256_hex(body), "bytes": len(body)}]
    histories: dict[str, dict] = {}
    if with_history:
        for f in summary.get("features", []):
            eid = f["id"]
            detail_url = (f.get("properties") or {}).get("detail")
            if not detail_url:
                histories[eid] = {"origin_versions": None, "note": "no detail URL in summary"}
                continue
            durl = detail_url + ("&" if "?" in detail_url else "?") + "includesuperseded=true"
            try:
                dbody = get(durl)
            except Exception as exc:  # recorded, not hidden
                histories[eid] = {"origin_versions": None, "note": f"detail fetch failed: {exc}"}
                continue
            responses.append({"url": durl, "sha256": sha256_hex(dbody), "bytes": len(dbody)})
            versions = _origin_versions(json.loads(dbody))
            histories[eid] = {"origin_versions": versions or None,
                              "note": "" if versions else "detail has no origin products"}
    return {"query": {"start": _iso(_parse_utc(start)), "end": _iso(_parse_utc(end)), "minmag": float(minmag)},
            "summary": summary, "histories": histories, "responses": responses, "fetched_at_utc": fetched_at}


def snapshot(raw: dict, path: str | Path) -> dict:
    """Write ``raw`` (from :func:`fetch_window`) with a provenance block; returns the document."""
    payload = {"schema": SNAPSHOT_SCHEMA, "query": raw["query"], "summary": raw["summary"],
               "histories": raw["histories"]}
    n_hist = sum(1 for h in raw["histories"].values() if h.get("origin_versions"))
    provenance = {
        "source_url": raw["responses"][0]["url"],
        "detail_urls": "each event's properties.detail URL with &includesuperseded=true",
        "fetched_at_utc": raw["fetched_at_utc"],
        "sha256": sha256_hex(canonical_bytes(payload)),
        "sha256_of": "canonical JSON (sorted keys, compact separators, UTF-8) of the 'payload' object",
        "license": LICENSE,
        "raw_responses": raw["responses"],
        "notes": [
            "Data: USGS Earthquake Hazards Program, ComCat via FDSN event web service. Public domain.",
            "summary: the FDSN query response, unmodified.",
            "histories: per event, the origin-product versions (current and superseded) from the detail "
            "document fetched with includesuperseded=true, reduced to source, code, status, updateTime, "
            "preferredWeight, magnitude, magnitude type, origin time, location, depth, review status. "
            "The full detail bodies are not stored; their SHA-256 is listed in raw_responses.",
            f"{n_hist} of {len(raw['histories'])} events have an origin-product history; for events without one "
            "the scenario uses the summary 'time' and 'updated' fields only (see the scenario provenance).",
            "Superseded product versions reflect what ComCat retains today; versions deleted from ComCat "
            "before the fetch are not visible here.",
        ],
    }
    doc = {"provenance": provenance, "payload": payload}
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=1, ensure_ascii=False, sort_keys=False) + "\n", encoding="utf-8")
    return doc


def load_snapshot(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def verify_snapshot(path_or_doc: str | Path | dict) -> bool:
    doc = path_or_doc if isinstance(path_or_doc, dict) else load_snapshot(path_or_doc)
    return sha256_hex(canonical_bytes(doc["payload"])) == doc["provenance"]["sha256"]


# ----------------------------------------------------------------------------- timeline

def _shown_magnitudes(versions: list[dict], end_ms: int) -> list[dict]:
    """Publication instants at which the shown (preferred) magnitude changes, inside the window."""
    current: dict[tuple, dict] = {}
    shown: list[dict] = []
    for v in versions:
        if v["update_time_ms"] > end_ms:
            break
        key = (v["source"], v["code"])
        if v["status"] == "DELETE":
            current.pop(key, None)
        else:
            current[key] = v
        cands = [c for c in current.values() if c["magnitude"] is not None]
        if not cands:
            continue
        pref = max(cands, key=lambda c: (c["preferred_weight"], c["update_time_ms"]))
        if not shown or round(pref["magnitude"], 2) != round(shown[-1]["magnitude"], 2):
            shown.append(dict(pref, published_ms=v["update_time_ms"]))
    return shown


def build_timeline(snap: dict) -> tuple[list[dict], list[dict]]:
    """REAL part only: (entries, excluded). Each entry is a report or a correction with its
    publication time in epoch ms and the magnitude ComCat showed from that instant."""
    payload = snap["payload"]
    start_ms = _epoch_ms(_parse_utc(payload["query"]["start"]))
    end_ms = _epoch_ms(_parse_utc(payload["query"]["end"]))
    entries: list[dict] = []
    excluded: list[dict] = []
    for f in sorted(payload["summary"]["features"], key=lambda f: (f["properties"]["time"], f["id"])):
        eid, props = f["id"], f["properties"]
        lon, lat, depth = (f.get("geometry") or {}).get("coordinates", [None, None, None])[:3]
        base = {"usgs_id": eid, "place": props.get("place"),
                "origin_time_utc": _ms_to_iso(int(props["time"])), "origin_time_ms": int(props["time"])}
        hist = (payload["histories"].get(eid) or {}).get("origin_versions")
        if hist:
            shown = _shown_magnitudes(hist, end_ms)
            if not shown:
                excluded.append({**base, "reason": "first origin product published after the window end "
                                                   "(later catalogue addition or review); not knowable in the window"})
                continue
            first = shown[0]
            entries.append({**base, "type": "report", "t_ms": first["published_ms"], "history": True,
                            "mag": first["magnitude"], "mag_type": first["magnitude_type"],
                            "contributor": first["source"], "lat": first["latitude"], "lon": first["longitude"],
                            "depth_km": first["depth_km"]})
            for n, s in enumerate(shown[1:], start=1):
                entries.append({**base, "type": "correction", "n": n, "t_ms": s["published_ms"], "history": True,
                                "mag": s["magnitude"], "mag_type": s["magnitude_type"], "contributor": s["source"],
                                "previous_mag": shown[n - 1]["magnitude"]})
        else:
            entries.append({**base, "type": "report", "t_ms": int(props["time"]), "history": False,
                            "mag": float(props["mag"]), "mag_type": props.get("magType"), "contributor": props.get("net"),
                            "lat": lat, "lon": lon, "depth_km": depth})
            upd = int(props.get("updated") or 0)
            if start_ms <= upd <= end_ms and upd > int(props["time"]):
                entries.append({**base, "type": "correction", "n": 1, "t_ms": upd, "history": False,
                                "mag": float(props["mag"]), "mag_type": props.get("magType"),
                                "contributor": props.get("net"), "previous_mag": None})
    entries = [e for e in entries if start_ms <= e["t_ms"] <= end_ms]
    entries.sort(key=lambda e: (e["t_ms"], e["usgs_id"], e.get("n", 0)))
    return entries, excluded


# ----------------------------------------------------------------------------- scenario

def _report_id(usgs_id: str) -> str:
    return f"quake-{usgs_id}"


def _correction_id(usgs_id: str, n: int) -> str:
    return f"corr-{usgs_id}-{n}"


def _rule_when(threshold: float) -> str:
    t = f"{threshold:g}"
    return (f"count(kind='quake_report', payload.mag>={t}, not_corrected) >= 1 "
            f"or count(kind='correction', payload.mag>={t}, not_corrected) >= 1")


def _inject(e: dict, start_ms: int) -> dict:
    at_ns = (e["t_ms"] - start_ms) * NS_PER_MS
    common = {"place": e["place"], "origin_time_utc": e["origin_time_utc"],
              "published_utc": _ms_to_iso(e["t_ms"]), "contributor": e["contributor"], "source": "usgs"}
    if e["type"] == "report":
        payload = {"id": e["usgs_id"], "mag": e["mag"], "mag_type": e["mag_type"], "depth_km": e["depth_km"],
                   "lat": e["lat"], "lon": e["lon"], **common}
        if not e["history"]:
            payload["note"] = ("no origin-product history available: published at origin time with the "
                               "final catalogue magnitude")
        return {"id": _report_id(e["usgs_id"]), "at_ns": at_ns, "source_id": "usgs_comcat", "kind": "quake_report",
                "payload": payload, "policy_labels": ["public"]}
    n = e["n"]
    corrects = _report_id(e["usgs_id"]) if n == 1 else _correction_id(e["usgs_id"], n - 1)
    prev = e["previous_mag"]
    if prev is None:
        note = "revision time from summary 'updated'; prior magnitude not available"
    else:
        note = f"{'upgrade' if e['mag'] > prev else 'downgrade'} {prev:g} -> {e['mag']:g}"
    payload = {"corrects": corrects, "report": _report_id(e["usgs_id"]), "id": e["usgs_id"], "mag": e["mag"],
               "mag_type": e["mag_type"], "previous_mag": prev, "note": note, **common}
    return {"id": _correction_id(e["usgs_id"], n), "at_ns": at_ns, "source_id": "usgs_comcat", "kind": "correction",
            "payload": payload, "policy_labels": ["public"], "priority": 5}


def _truth_value_flips(injects: list[dict], when: str) -> list[dict]:
    """Corrections that change the rule's value over truth (evaluated at their own instant)."""
    from worldline.core import Event
    from worldline.worlds.predicate import parse_predicate

    pred = parse_predicate(when)
    evs = sorted((Event(id=i["id"], scenario_id="tmp", source_id=i["source_id"], event_time_ns=i["at_ns"],
                        kind=i["kind"], payload=i["payload"], received_time_ns=i["at_ns"],
                        priority=i.get("priority", 0)) for i in injects), key=Event.sort_key)
    flips, prev = [], pred.evaluate([], [])
    for k, ev in enumerate(evs):
        now = pred.evaluate(evs[:k + 1], evs[:k + 1])
        if now != prev and ev.kind == "correction":
            flips.append({"id": ev.id, "at_ns": ev.event_time_ns, "value_after": now})
        prev = now
    return flips


def _fmt_s(ns: int) -> str:
    return f"{ns / NS_PER_S:.3f}s"


def to_scenario(snapshot_path: str | Path, out_yaml: str | Path, *, threshold: float = 8.5,
                decision_at_s: int | None = None, decision_margin_s: int = 30,
                field_ops: tuple[int, int] = (5, 2), regional_hq: tuple[int, int] = (60, 30),
                seed: int = 20250729, scenario_id: str | None = None,
                snapshot_ref: str | None = None) -> "Any":
    """Snapshot -> scenario YAML (written to ``out_yaml``); returns the loaded Scenario.

    ``field_ops`` / ``regional_hq`` are ``(base_latency_s, jitter_s)`` (MODEL). The decision time
    (MODEL) defaults to: publication time of the first real correction that flips the rule over
    truth + regional_hq base latency + jitter + ``decision_margin_s``, rounded up to a whole second,
    so that in the baseline the correction always arrives before the decision and the latent defect
    "decided on a stale magnitude" needs only a delay of that correction.
    """
    from worldline.worlds.scenario import scenario_from_dict

    snap = load_snapshot(snapshot_path)
    if not verify_snapshot(snap):
        raise ValueError(f"snapshot {snapshot_path} fails its own SHA-256; refusing to build a scenario from it")
    q = snap["payload"]["query"]
    start_ms = _epoch_ms(_parse_utc(q["start"]))
    end_ms = _epoch_ms(_parse_utc(q["end"]))
    key = window_key(q["start"], q["end"], q["minmag"])
    sid = scenario_id or "usgs_" + key.replace(".", "_")
    entries, excluded = build_timeline(snap)
    injects = [_inject(e, start_ms) for e in entries]
    when = _rule_when(threshold)
    flips = _truth_value_flips(injects, when)
    horizon_ns = (end_ms - start_ms) * NS_PER_MS
    hq_base, hq_jit = regional_hq
    if decision_at_s is not None:
        decision_ns = int(decision_at_s) * NS_PER_S
        decision_note = f"decision time set explicitly to {decision_at_s}s after window start"
    elif flips:
        f0 = flips[0]
        decision_ns = math.ceil((f0["at_ns"] + (hq_base + hq_jit + decision_margin_s) * NS_PER_S) / NS_PER_S) * NS_PER_S
        decision_note = (f"decision time = publication of {f0['id']} ({_fmt_s(f0['at_ns'])}) + regional_hq "
                         f"base {hq_base}s + jitter {hq_jit}s + margin {decision_margin_s}s, rounded up")
    else:
        decision_ns = horizon_ns // 2 // NS_PER_S * NS_PER_S
        decision_note = "no real correction flips the rule inside the window: decision placed mid-window, defect absent"
    decision_ns = min(decision_ns, horizon_ns)

    n_rep = sum(1 for i in injects if i["kind"] == "quake_report")
    n_cor = len(injects) - n_rep
    prov = snap["provenance"]
    provenance = {
        "real": ["event ids; place labels (today's catalogue text); locations and depths as first published",
                 "first-published magnitudes and their publication times",
                 "magnitude revisions (corrections) and their publication times", "origin times"],
        "model": ["roles field_ops, regional_hq, evaluator and their delivery latency/jitter/ordering",
                  "actor duty_seismologist, its decision rule, threshold and decision time",
                  "scenario seed (only drives the modelled delivery jitter)"],
        "source_url": prov["source_url"], "fetched_at_utc": prov["fetched_at_utc"],
        "snapshot": snapshot_ref or Path(snapshot_path).as_posix(), "snapshot_sha256": prov["sha256"],
        "license": prov["license"],
        "window_utc": {"start": q["start"], "end": q["end"], "minmag": q["minmag"]},
        "time_mapping": "at_ns = (USGS publication time - window start) in ns; millisecond resolution from USGS",
        "magnitude_model": ("shown magnitude = current origin product with highest preferredWeight (ties: latest "
                            "update), approximating ComCat preferred-origin selection; each change in the window "
                            "is one correction; corrects = the previous estimate (report or prior correction)"),
        "counts": {"reports": n_rep, "corrections": n_cor, "excluded_events": len(excluded),
                   "events_without_history": sum(1 for e in entries if e["type"] == "report" and not e["history"])},
        "excluded": [{"usgs_id": x["usgs_id"], "reason": x["reason"]} for x in excluded],
        "rule_flipping_corrections": [{"id": f["id"], "at_ns": f["at_ns"], "rule_value_after": f["value_after"]}
                                      for f in flips],
        "decision_time": decision_note,
    }
    doc = {
        "id": sid, "seed": seed,
        "clock": {"epoch_ns": 0, "tick_ns": "1s", "horizon_ns": horizon_ns},
        "provenance": provenance,
        "roles": [
            {"id": "field_ops", "visibility": ["*"],
             "delivery": {"base_latency_ns": f"{field_ops[0]}s", "jitter_ns": f"{field_ops[1]}s",
                          "drop_probability": 0.0, "ordering": "fifo"}},
            {"id": "regional_hq", "visibility": ["*"],
             "delivery": {"base_latency_ns": f"{hq_base}s", "jitter_ns": f"{hq_jit}s",
                          "drop_probability": 0.0, "ordering": "network"}},
            {"id": "evaluator", "visibility": ["*"],
             "delivery": {"base_latency_ns": 0, "jitter_ns": 0, "drop_probability": 0.0, "ordering": "fifo"}},
        ],
        "actors": [{"id": "duty_seismologist", "role": "regional_hq", "decision_rules": [{
            "id": "advisory_or_hold", "at_ns": decision_ns, "window_ns": decision_ns, "when": when,
            "then": "issue_advisory", "otherwise": "hold"}]}],
        "injects": injects,
        "transitions": [],
        "invariants": ["epistemic.no_future_knowledge", "decision.consistent_with_truth",
                       "absence.no_corrupted_in_decision"],
    }
    header = [
        f"# WORLDLINE scenario generated from REAL recorded USGS data. Do not edit by hand:",
        f"#   uv run worldline real usgs --start {q['start']} --end {q['end']} --minmag {q['minmag']:g} --offline",
        "#",
        "# REAL (USGS ComCat, public domain; snapshot " + (snapshot_ref or Path(snapshot_path).as_posix()) + "):",
        "#   which events occurred, ids, place labels (today's catalogue text), first-published locations and",
        "#   depths, origin times; the first-published",
        "#   magnitude of each event and every later magnitude revision, each at its real USGS publication",
        "#   time (at_ns = publication time - window start, ms resolution). quake_report = first",
        "#   publication; correction = a later change of the shown magnitude.",
        f"#   window {q['start']} .. {q['end']}, minmagnitude {q['minmag']:g}; snapshot sha256 {prov['sha256']}",
        "#",
        "# MODEL (declared, NOT real; no claim that any agency operates this way):",
        f"#   roles field_ops (fifo {field_ops[0]}s +{field_ops[1]}s), regional_hq (network {hq_base}s +{hq_jit}s jitter),",
        "#   evaluator (0s); actor duty_seismologist and its rule (threshold M" + f"{threshold:g}" + ") and decision time.",
        f"#   {decision_note}.",
        "#   'issue_advisory' / 'hold' are labels of the model, not real advisories.",
        "#",
        "# Latent defect (present in the real timeline, exposed only by a modelled delay): if the real",
        "# rule-flipping correction reaches regional_hq after the decision time, the duty seismologist",
        "# decides on a stale magnitude and decision.consistent_with_truth FAILs.",
    ]
    body = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=200, default_flow_style=None)
    out = Path(out_yaml)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(header) + "\n" + body, encoding="utf-8")
    return scenario_from_dict(yaml.safe_load(out.read_text(encoding="utf-8")))
