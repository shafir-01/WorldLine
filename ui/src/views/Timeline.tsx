import { useEffect, useMemo, useRef, useState } from "react";
import type { LoadedData, Trace, WEvent } from "../types";
import { useTrace, type TraceEntry, type TraceGroup } from "../data";
import { causalDiff } from "../diff";
import { NS, secs, shortBranch, tClock } from "../format";
import { ScheduleList } from "../components/Schedule";
import { VerdictPill } from "../components/Badges";

interface Props {
  data: LoadedData;
  registry: Map<string, TraceEntry>;
  selected: string;
  onSelect: (id: string) => void;
}

const ROLE_ORDER = ["harbour_ops", "coast_guard", "ops_center", "convoy_lead", "evaluator"];
const KIND_ORDER = ["sighting", "ais_position", "status", "capacity", "weather", "alert", "ack", "acknowledged", "correction", "decision"];
const LANE_PAD = 14;
const SUBROW = 16;
const LEFT = 235;
const RIGHT = 24;
const AXIS_H = 28;
const GROUP_LABEL: Record<TraceGroup, string> = { root: "Baseline", minimal: "Minimal counterexample (ddmin)", challenge: "Genome challenge", guided: "Failing branches: guided", random: "Failing branches: random", other: "Other traces" };
const GROUP_ORDER: TraceGroup[] = ["root", "minimal", "challenge", "guided", "random", "other"];

function roleOfDecision(trace: Trace, d: WEvent): string {
  const actor = String(d.payload.actor ?? d.source_id);
  const a = trace.scenario?.actors?.find((x) => x.id === actor);
  if (a && trace.views[a.role]) return a.role;
  const role = Object.keys(trace.views).find((r) => actor.startsWith(r));
  return role ?? Object.keys(trace.views)[0];
}

function horizonOf(trace: Trace): number {
  return trace.scenario?.clock?.horizon_ns ?? trace.clock_end.now_ns;
}

function useWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(900);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => setW(Math.max(480, Math.floor(entries[0].contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

export default function Timeline({ registry, selected, onSelect }: Props) {
  const { trace, verdicts, loading, available, entry } = useTrace(registry, selected);
  const root = useTrace(registry, "root");
  const [compare, setCompare] = useState(true);
  const [t, setT] = useState<number | null>(null); // AS_KNOWN_AT scrubber, null = end of horizon
  const [picked, setPicked] = useState<{ role: string; event: WEvent } | null>(null);
  const [wrapRef, width] = useWidth<HTMLDivElement>();

  const witness = useMemo(() => new Set(verdicts.filter((v) => v.result === "FAIL").flatMap((v) => v.witness)), [verdicts]);
  const canCompare = !!(trace && root.trace && trace.branch_id !== "root" && trace.scenario_id === root.trace.scenario_id);
  const diff = useMemo(() => (compare && canCompare && trace && root.trace ? causalDiff(root.trace, trace) : null), [compare, canCompare, trace, root.trace]);

  useEffect(() => {
    setPicked(null);
    setT(null);
  }, [selected]);

  const head = <Head selected={selected} registry={registry} onSelect={onSelect} />;
  if (!available) {
    return (
      <div className="view">
        {head}
        <div className="panel empty">
          No trace file was exported for <code>{selected}</code>. The pipeline writes traces for <b>root</b>, every <b>failing</b> branch, the <b>minimal</b> counterexample and the <b>challenge</b> counterexample. Pick one above or select a failing
          node in Worlds.
        </div>
      </div>
    );
  }
  if (loading) {
    return (
      <div className="view">
        {head}
        <div className="panel empty">Loading trace…</div>
      </div>
    );
  }
  if (!trace) {
    return (
      <div className="view">
        {head}
        <div className="panel empty error">
          The trace file <code>{entry?.traceFile}</code> is listed in the data but could not be fetched. The fixture snapshot ships only a subset of traces; build against <code>experiments/out</code> for all of them.
        </div>
      </div>
    );
  }

  const horizon = horizonOf(trace);
  const tKnown = t ?? horizon;
  const roles = [...ROLE_ORDER.filter((r) => trace.views[r]), ...Object.keys(trace.views).filter((r) => !ROLE_ORDER.includes(r))];
  const roleSpec = new Map((trace.scenario?.roles ?? []).map((r) => [r.id, r]));
  const kindsPerRole = new Map<string, string[]>();
  for (const r of roles) {
    const ks = new Set(trace.views[r].events.map((e) => e.kind));
    for (const d of trace.decisions) if (roleOfDecision(trace, d) === r) ks.add("decision");
    kindsPerRole.set(r, [...KIND_ORDER.filter((k) => ks.has(k)), ...[...ks].filter((k) => !KIND_ORDER.includes(k)).sort()]);
  }
  const laneH = (r: string) => LANE_PAD * 2 + kindsPerRole.get(r)!.length * SUBROW;
  const laneTop = new Map<string, number>();
  let y = AXIS_H;
  for (const r of roles) {
    laneTop.set(r, y);
    y += laneH(r) + 6;
  }
  const height = y + 8;
  const plotW = width - LEFT - RIGHT;
  const X = (ns: number) => LEFT + (Math.max(0, Math.min(horizon, ns)) / horizon) * plotW;
  const Y = (r: string, kind: string) => laneTop.get(r)! + LANE_PAD + kindsPerRole.get(r)!.indexOf(kind) * SUBROW + SUBROW / 2;
  const tickStep = horizon > 2400 * NS ? 300 * NS : 120 * NS;
  const ticks = Array.from({ length: Math.floor(horizon / tickStep) + 1 }, (_, i) => i * tickStep);

  const rootView = (role: string) => new Map((canCompare && compare ? root.trace?.views[role]?.events ?? [] : []).map((e) => [e.id, e]));
  const rootDecisions = new Map((canCompare && compare ? root.trace?.decisions ?? [] : []).map((d) => [String(d.payload.rule_id), d]));
  const showCompare = compare && canCompare;

  return (
    <div className="view">
      {head}
      <div className="panel">
        <div className="toolbar">
          <label className="scrub">
            <span>
              AS_KNOWN_AT(<b>{tClock(tKnown)}</b>) {t === null && <span className="muted">(end of horizon)</span>}
            </span>
            <input type="range" min={0} max={horizon} step={NS} value={tKnown} onChange={(e) => setT(Number(e.target.value))} aria-label="AS_KNOWN_AT scrubber" />
            <button className="btn btn-sm" onClick={() => setT(null)} disabled={t === null}>
              reset
            </button>
          </label>
          <label
            className={`toggle ${canCompare ? "" : "disabled"}`}
            title={trace.branch_id === "root" ? "Select a non-root branch to compare against root" : canCompare ? "Overlay root received-times and show the causal diff" : "This trace is from a different scenario; no baseline trace to compare against"}
          >
            <input type="checkbox" checked={showCompare} disabled={!canCompare} onChange={(e) => setCompare(e.target.checked)} />
            Compare with root
          </label>
          <div className="legend legend-right">
            <span>
              <svg width="22" height="12">
                <circle cx="5" cy="6" r="3.5" className="ev-mark" />
                <line x1="8" y1="6" x2="20" y2="6" className="lag-line" />
              </svg>{" "}
              event_time → received_time (information lag)
            </span>
            <span>
              <svg width="14" height="14">
                <path d="M7 1L13 7 7 13 1 7z" className="dec-mark" />
              </svg>{" "}
              decision point
            </span>
            <span>
              <svg width="14" height="14">
                <circle cx="7" cy="7" r="5" className="wit-ring" />
              </svg>{" "}
              FAIL witness
            </span>
            {showCompare && (
              <span>
                <svg width="22" height="12">
                  <circle cx="5" cy="6" r="3.5" className="root-mark" />
                  <line x1="8" y1="6" x2="20" y2="6" className="delta-line" />
                </svg>{" "}
                root received_time → branch received_time
              </span>
            )}
          </div>
        </div>

        <div ref={wrapRef} className="tl-wrap">
          <svg className="tl" width={width} height={height} role="img" aria-label="per-role timelines">
            {ticks.map((tk) => (
              <g key={tk} transform={`translate(${X(tk)},0)`}>
                <line y1={AXIS_H - 6} y2={height} className="grid" />
                <text y={AXIS_H - 10} className="axis-label" textAnchor="middle">
                  {tClock(tk)}
                </text>
              </g>
            ))}
            {roles.map((r) => {
              const top = laneTop.get(r)!;
              const h = laneH(r);
              const view = trace.views[r].events;
              const rv = showCompare ? rootView(r) : null;
              const spec = roleSpec.get(r);
              return (
                <g key={r} className="lane">
                  <rect x={0} y={top} width={width} height={h} className="lane-bg" />
                  <text x={10} y={top + 16} className="lane-title">
                    {r}
                  </text>
                  {spec && (
                    <text x={10} y={top + 30} className="lane-sub">
                      latency {secs(spec.delivery.base_latency_ns)} ± {secs(spec.delivery.jitter_ns)} · {spec.delivery.ordering}
                      {spec.delivery.drop_probability > 0 ? ` · drop ${spec.delivery.drop_probability}` : ""}
                    </text>
                  )}
                  {spec && spec.visibility.length > 0 && (
                    <text x={10} y={top + 43} className="lane-sub">
                      sees {spec.visibility.join(", ")}
                    </text>
                  )}
                  {kindsPerRole.get(r)!.map((k) => (
                    <text key={k} x={LEFT - 8} y={Y(r, k) + 4} className="kind-label" textAnchor="end">
                      {k}
                    </text>
                  ))}
                  {rv &&
                    view.map((e) => {
                      const o = rv.get(e.id);
                      if (!o || o.received_time_ns == null || e.received_time_ns == null || o.received_time_ns === e.received_time_ns) return null;
                      const yy = Y(r, e.kind);
                      return (
                        <g key={`root-${e.id}`} className="overlay">
                          <line x1={X(o.received_time_ns)} x2={X(e.received_time_ns)} y1={yy} y2={yy} className="delta-line" />
                          <circle cx={X(o.received_time_ns)} cy={yy} r={3.5} className="root-mark" />
                        </g>
                      );
                    })}
                  {rv &&
                    [...rv.values()]
                      .filter((o) => !view.some((e) => e.id === o.id))
                      .map((o) => {
                        const k = kindsPerRole.get(r)!.includes(o.kind) ? o.kind : kindsPerRole.get(r)![0];
                        return (
                          <g key={`only-root-${o.id}`} className="overlay only-root" opacity={0.7}>
                            <circle cx={X(o.event_time_ns)} cy={Y(r, k)} r={4} className="root-mark" />
                            <text x={X(o.event_time_ns) + 6} y={Y(r, k) - 5} className="tiny">
                              dropped in branch
                            </text>
                          </g>
                        );
                      })}
                  {view.map((e, idx) => {
                    const yy = Y(r, e.kind);
                    const recv = e.received_time_ns ?? e.event_time_ns;
                    const known = recv <= tKnown;
                    const isWit = witness.has(e.id) || witness.has(e.id.split("#")[0]);
                    const isPicked = picked?.event === e && picked.role === r;
                    return (
                      <g
                        key={`${e.id}-${idx}`}
                        className={`ev ${known ? "" : "ev-unknown"} ${isWit ? "ev-witness" : ""} ${isPicked ? "ev-picked" : ""} ev-kind-${e.kind}`}
                        onClick={() => setPicked({ role: r, event: e })}
                        role="button"
                        tabIndex={0}
                        onKeyDown={(k) => k.key === "Enter" && setPicked({ role: r, event: e })}
                      >
                        <title>{`${e.id} ${e.kind} from ${e.source_id}\nevent ${tClock(e.event_time_ns)} → received ${tClock(recv)} (lag ${secs(recv - e.event_time_ns)})`}</title>
                        <line x1={X(e.event_time_ns)} x2={X(recv)} y1={yy} y2={yy} className="lag-line" />
                        <line x1={X(recv)} x2={X(recv)} y1={yy - 4} y2={yy + 4} className="recv-tick" />
                        {isWit && <circle cx={X(e.event_time_ns)} cy={yy} r={7.5} className="wit-ring" />}
                        <circle cx={X(e.event_time_ns)} cy={yy} r={3.5} className="ev-mark" />
                      </g>
                    );
                  })}
                  {trace.decisions
                    .filter((d) => roleOfDecision(trace, d) === r)
                    .map((d) => {
                      const at = Number(d.payload.at_ns);
                      const yy = Y(r, "decision");
                      const rd = showCompare ? rootDecisions.get(String(d.payload.rule_id)) : undefined;
                      const differs = rd && rd.payload.option !== d.payload.option;
                      const isPicked = picked?.event.id === d.id;
                      return (
                        <g key={d.id} className={`dec ${at <= tKnown ? "" : "ev-unknown"} ${differs ? "dec-differs" : ""} ${isPicked ? "ev-picked" : ""}`} onClick={() => setPicked({ role: r, event: d })} role="button" tabIndex={0}>
                          <title>{`${d.payload.rule_id} @ ${tClock(at)} → ${d.payload.option}${differs ? ` (root: ${rd!.payload.option})` : ""}`}</title>
                          <line x1={X(at)} x2={X(at)} y1={top} y2={top + h} className="dec-line" />
                          <path d={`M${X(at)} ${yy - 6}L${X(at) + 6} ${yy}L${X(at)} ${yy + 6}L${X(at) - 6} ${yy}z`} className="dec-mark" />
                          <text x={X(at) + 9} y={yy + 4} className="dec-label">
                            {String(d.payload.rule_id)} → <tspan className="dec-opt">{String(d.payload.option)}</tspan>
                            {differs && <tspan className="dec-root"> (root: {String(rd!.payload.option)})</tspan>}
                          </text>
                        </g>
                      );
                    })}
                </g>
              );
            })}
            <g transform={`translate(${X(tKnown)},0)`} className="scrub-line">
              <line y1={AXIS_H - 6} y2={height} />
              <text y={height - 2} x={X(tKnown) > width - 110 ? -4 : 4} textAnchor={X(tKnown) > width - 110 ? "end" : "start"} className="tiny">
                known at {tClock(tKnown)}
              </text>
            </g>
          </svg>
        </div>
      </div>

      <div className="split">
        <div className="panel">
          <h3>Branch</h3>
          <code className="block">{trace.branch_id}</code>
          <div className="small muted">
            scenario <b>{trace.scenario_id}</b> · seed {trace.seed} · trace digest <code title={trace.digest}>{trace.digest.replace(/^sha256:/, "").slice(0, 16)}…</code>
          </div>
          <h4>Fault schedule</h4>
          <ScheduleList schedule={entry?.schedule ?? []} empty={trace.branch_id === "root" ? "(empty schedule: baseline world)" : "(schedule not recorded for this trace)"} />
          <h3>Verdicts</h3>
          {verdicts.length === 0 && <div className="muted small">no verdict file for this trace</div>}
          <ul className="verdicts compact">
            {verdicts.map((v) => (
              <li key={v.invariant_id} className={`verdict verdict-${v.result.toLowerCase()}`}>
                <div className="verdict-head">
                  <VerdictPill v={v.result} /> <span className="inv">{v.invariant_id}</span>
                </div>
                {v.result !== "PASS" && <div className="small">{v.explanation}</div>}
                {v.result === "FAIL" && v.witness.length > 0 && (
                  <div className="small">
                    witness:{" "}
                    {v.witness.map((w) => (
                      <code key={w} className="wit">
                        {w}
                      </code>
                    ))}
                  </div>
                )}
                {v.missing_data.length > 0 && <div className="small muted">missing signal: {v.missing_data.join(", ")}</div>}
              </li>
            ))}
          </ul>
        </div>

        <div className="panel">
          <h3>{picked ? "Event" : "Pick an event"}</h3>
          {picked ? <EventCard role={picked.role} e={picked.event} witness={witness} tKnown={tKnown} /> : <p className="muted small">Click a marker or diamond to inspect its payload, provenance and information lag.</p>}
        </div>

        {diff && (
          <div className="panel diff">
            <h3>
              Causal diff <span className="muted">root → {shortBranch(trace.branch_id)}</span>
            </h3>
            <h4>Decisions that differ ({diff.decisions.length})</h4>
            {diff.decisions.length === 0 && <div className="muted small">none</div>}
            <ul className="plain">
              {diff.decisions.map((d) => (
                <li key={d.rule_id} className="small">
                  <b>{d.rule_id}</b> @ {tClock(d.at_ns)}: <code>{d.option_a ?? "—"}</code> → <code className="bad">{d.option_b ?? "—"}</code>
                  {d.known_only_a.length > 0 && <div className="muted">known only in root: {d.known_only_a.join(", ")}</div>}
                  {d.known_only_b.length > 0 && <div className="muted">known only in branch: {d.known_only_b.join(", ")}</div>}
                </li>
              ))}
            </ul>
            <h4>Received time moved ({diff.moved.length})</h4>
            {diff.moved.length === 0 && <div className="muted small">none</div>}
            <ul className="plain">
              {diff.moved.map((m) => (
                <li key={`${m.role}/${m.id}`} className="small">
                  <code>{m.id}</code> {m.kind} in <b>{m.role}</b>: {tClock(m.from_ns)} → {tClock(m.to_ns)}{" "}
                  <span className={m.delta_ns > 0 ? "bad" : "good"}>
                    ({m.delta_ns > 0 ? "+" : ""}
                    {secs(m.delta_ns)})
                  </span>
                </li>
              ))}
            </ul>
            {(diff.only_in_a.length > 0 || diff.only_in_b.length > 0) && (
              <>
                <h4>Presence</h4>
                <ul className="plain">
                  {diff.only_in_a.map((x) => (
                    <li key={`a-${x.role}-${x.event.id}`} className="small">
                      <code>{x.event.id}</code> {x.event.kind} only in root (<b>{x.role}</b>)
                    </li>
                  ))}
                  {diff.only_in_b.map((x) => (
                    <li key={`b-${x.role}-${x.event.id}`} className="small">
                      <code>{x.event.id}</code> {x.event.kind} only in branch (<b>{x.role}</b>)
                    </li>
                  ))}
                </ul>
              </>
            )}
            {diff.corrupted.length > 0 && (
              <>
                <h4>Payload differs</h4>
                <ul className="plain">
                  {diff.corrupted.map((c) => (
                    <li key={`${c.role}/${c.id}`} className="small">
                      <code>{c.id}</code> in <b>{c.role}</b>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function Head({ selected, registry, onSelect }: { selected: string; registry: Map<string, TraceEntry>; onSelect: (id: string) => void }) {
  const groups = GROUP_ORDER.map((g) => ({ g, entries: [...registry.values()].filter((e) => e.group === g) })).filter((x) => x.entries.length);
  const known = registry.has(selected);
  return (
    <header className="view-head">
      <div>
        <h1>Timeline</h1>
        <p className="lede">
          One lane per role over virtual time. A marker sits at <code>event_time_ns</code> (truth) and its thin line runs to <code>received_time_ns</code> (when that role learned of it). Decisions evaluate only what was received before{" "}
          <code>at_ns</code>; a FAIL witness is what the actor had not yet received.
        </p>
      </div>
      <label className="select">
        trace
        <select value={known ? selected : ""} onChange={(e) => onSelect(e.target.value)}>
          {!known && <option value="">{selected} (no trace)</option>}
          {groups.map(({ g, entries }) => (
            <optgroup key={g} label={GROUP_LABEL[g]}>
              {entries.map((e) => (
                <option key={e.key} value={e.key}>
                  {g === "guided" || g === "random" ? `${shortBranch(e.key)} · ${(e.failingInvariants ?? []).join(", ")}` : e.label}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </label>
    </header>
  );
}

function EventCard({ role, e, witness, tKnown }: { role: string; e: WEvent; witness: Set<string>; tKnown: number }) {
  const recv = e.received_time_ns ?? e.event_time_ns;
  return (
    <div className="event-card">
      <div className="ev-title">
        <code>{e.id}</code> <span className="chip">{e.kind}</span> {witness.has(e.id) && <span className="pill pill-fail">witness</span>}
        {recv > tKnown && <span className="pill pill-unknown">not yet known at scrubber</span>}
      </div>
      <dl className="kv">
        <dt>role view</dt>
        <dd>{role}</dd>
        <dt>source</dt>
        <dd>{e.source_id}</dd>
        <dt>event_time</dt>
        <dd>{tClock(e.event_time_ns)}</dd>
        <dt>received_time</dt>
        <dd>
          {tClock(recv)} <span className="muted">(lag {secs(recv - e.event_time_ns)})</span>
        </dd>
        {e.policy_labels.length > 0 && (
          <>
            <dt>labels</dt>
            <dd>{e.policy_labels.join(", ")}</dd>
          </>
        )}
        {e.provenance_ids.length > 0 && (
          <>
            <dt>provenance</dt>
            <dd>{e.provenance_ids.join(", ")}</dd>
          </>
        )}
        {e.quality != null && (
          <>
            <dt>quality</dt>
            <dd>{e.quality}</dd>
          </>
        )}
      </dl>
      <pre className="json">{JSON.stringify(e.payload, null, 2)}</pre>
    </div>
  );
}
