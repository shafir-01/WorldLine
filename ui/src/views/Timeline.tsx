import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { LoadedData, Trace, VerdictRecord, WEvent } from "../types";
import { useTrace, type TraceEntry, type TraceGroup } from "../data";
import { causalDiff, diffSummary } from "../diff";
import { NS, eventSummary, humanDuration, humanName, interventionPlain, invariantHelp, invariantName, mmss, opName, parseDecisionExplanation, secs, shortBranch, verdictSentence } from "../format";
import { VerdictGlyph, VerdictPill } from "../components/Badges";
import { ShortId } from "../components/ShortId";
import { Check, Chip, ContextBar, Ctx, Disclosure, HoverCard, keepScroll, Tip, ViewIntro } from "../components/Inspect";
import "../inspect.css";

interface Props {
  data: LoadedData;
  registry: Map<string, TraceEntry>;
  selected: string;
  onSelect: (id: string) => void;
}

const ROLE_ORDER = ["harbour_ops", "coast_guard", "ops_center", "convoy_lead", "evaluator"];
const KIND_ORDER = ["sighting", "threat_report", "correction", "alert", "ack", "status", "ais_position", "weather", "decision"];
const HEAD_W = 216; // HTML lane header column
const KIND_W = 96; // kind labels, right-aligned against the plot
const LEFT = HEAD_W + KIND_W;
const RIGHT = 28;
const AXIS_H = 30;
const LANE_PAD = 10;
const SUBROW = 17;
const LANE_MIN = 104;
const GROUP_LABEL: Record<TraceGroup, string> = { root: "Baseline", minimal: "Minimal counterexample", challenge: "Genome challenge", guided: "Failing worlds: guided", random: "Failing worlds: random", windowed: "Failing worlds: windowed", other: "Other traces" };
const GROUP_ORDER: TraceGroup[] = ["root", "minimal", "challenge", "guided", "random", "windowed", "other"];

function roleOfDecision(trace: Trace, d: WEvent): string {
  const actor = String(d.payload.actor ?? d.source_id);
  const a = trace.scenario?.actors?.find((x) => x.id === actor);
  if (a && trace.views[a.role]) return a.role;
  const role = Object.keys(trace.views).find((r) => actor.startsWith(r));
  return role ?? Object.keys(trace.views)[0];
}

function useWidth(): [(el: HTMLDivElement | null) => void, number] {
  const [w, setW] = useState(1100);
  const ro = useRef<ResizeObserver | null>(null);
  const ref = useCallback((el: HTMLDivElement | null) => {
    ro.current?.disconnect();
    if (!el) return;
    ro.current = new ResizeObserver((entries) => setW(Math.max(640, Math.floor(entries[0].contentRect.width))));
    ro.current.observe(el);
  }, []);
  return [ref, w];
}

const kindWord = (k: string) => humanName(k).replace(/^ais position$/, "AIS position");

/** The decision the verdicts blame (if any), with the ids the actor had not received. */
function blamedDecision(verdicts: VerdictRecord[]) {
  const v = verdicts.find((x) => x.result === "FAIL" && x.invariant_id === "decision.consistent_with_truth");
  if (!v) return null;
  const p = parseDecisionExplanation(v.explanation);
  const missing = v.explanation.match(/had not received \[([^\]]*)\]/)?.[1]?.match(/'([^']+)'/g)?.map((s) => s.slice(1, -1)) ?? v.witness.filter((w) => !w.startsWith("decision:"));
  const decisionId = v.witness.find((w) => w.startsWith("decision:")) ?? (p.actor ? `decision:${p.actor}:` : "");
  return { v, ...p, missing, decisionId };
}

export default function Timeline({ registry, selected, onSelect }: Props) {
  const { trace, verdicts, loading, available, entry } = useTrace(registry, selected);
  const root = useTrace(registry, "root");
  const [compare, setCompare] = useState(true);
  const [t, setT] = useState<number | null>(null); // "known at" scrubber; null = end of horizon
  const [picked, setPicked] = useState<{ role: string; event: WEvent } | null>(null);
  const [hover, setHover] = useState<{ role: string; event: WEvent; x: number; y: number } | null>(null);
  const [preset, setPreset] = useState<"relevant" | "all" | "custom">("relevant");
  const [custom, setCustom] = useState<Set<string>>(new Set());
  const [zoom, setZoom] = useState(false);
  const [wrapRef, width] = useWidth();

  const select = (id: string) => keepScroll(() => onSelect(id));
  const witness = useMemo(() => new Set(verdicts.filter((v) => v.result === "FAIL").flatMap((v) => v.witness)), [verdicts]);
  const canCompare = !!(trace && root.trace && trace.branch_id !== "root" && trace.scenario_id === root.trace.scenario_id);
  const showCompare = compare && canCompare;
  const diff = useMemo(() => (showCompare && trace && root.trace ? causalDiff(root.trace, trace) : null), [showCompare, trace, root.trace]);

  useEffect(() => {
    setPicked(null);
    setHover(null);
    setT(null);
  }, [selected]);

  const head = <TraceContext selected={selected} registry={registry} onSelect={select} trace={trace} compare={showCompare} canCompare={canCompare} setCompare={setCompare} rootBranch={trace?.branch_id === "root"} />;
  const intro = (answer: React.ReactNode) => <ViewIntro eyebrow="Inspect · Timeline" question="What did each role know, and when?" answer={answer} />;

  if (!available || loading || !trace) {
    const msg = !available ? (
      <>
        No timeline was exported for <code>{shortBranch(selected)}</code>. The pipeline writes one for the baseline, every failing world, the minimal counterexample and the challenge counterexample. Pick one from the trace menu above.
      </>
    ) : loading ? (
      "Loading the timeline…"
    ) : (
      <>
        The file <code>{entry?.traceFile}</code> is listed in the data but could not be fetched. The fixture snapshot ships only some traces; build against <code>experiments/out</code> for all of them.
      </>
    );
    return (
      <div className="view ix-view">
        {intro("Each lane is one role. A dot is when something happened; its tail runs to when that role heard about it.")}
        {head}
        <div className={`panel empty ${!available || loading ? "" : "error"}`}>{msg}</div>
      </div>
    );
  }

  // ------------------------------------------------------------ derived structure
  const horizon = trace.scenario?.clock?.horizon_ns ?? trace.clock_end.now_ns;
  const roles = [...ROLE_ORDER.filter((r) => trace.views[r]), ...Object.keys(trace.views).filter((r) => !ROLE_ORDER.includes(r))];
  const eventById = new Map<string, WEvent>();
  for (const e of trace.truth.events) eventById.set(e.id, e);
  for (const r of roles) for (const e of trace.views[r].events) if (!eventById.has(e.id)) eventById.set(e.id, e);

  const allKinds = new Map<string, number>();
  for (const r of roles) for (const e of trace.views[r].events) allKinds.set(e.kind, (allKinds.get(e.kind) ?? 0) + 1);
  const rules = (trace.scenario?.actors ?? []).flatMap((a) => a.decision_rules.map((rule) => ({ actor: a, rule })));
  const relevant = new Set<string>();
  for (const { rule } of rules) for (const m of rule.when.matchAll(/kind='([^']+)'/g)) relevant.add(m[1]);
  for (const w of witness) {
    const e = eventById.get(w) ?? eventById.get(w.split("#")[0]);
    if (e) relevant.add(e.kind);
  }
  for (const iv of entry?.schedule ?? []) {
    const e = iv.target.event_id ? eventById.get(iv.target.event_id) : undefined;
    if (e) relevant.add(e.kind);
    if (iv.target.kind) relevant.add(String(iv.target.kind));
  }
  if (allKinds.has("correction")) relevant.add("correction");
  for (const k of [...relevant]) if (!allKinds.has(k)) relevant.delete(k);
  const shown = preset === "all" ? new Set(allKinds.keys()) : preset === "relevant" ? relevant : custom;
  const orderKinds = (ks: Iterable<string>) => {
    const s = new Set(ks);
    return [...KIND_ORDER.filter((k) => s.has(k)), ...[...s].filter((k) => !KIND_ORDER.includes(k)).sort()];
  };

  const blamed = blamedDecision(verdicts);
  const focusDecision = (blamed && trace.decisions.find((d) => d.id === blamed.decisionId || String(d.payload.actor) === blamed.actor)) ?? trace.decisions[0];
  const focusRule = focusDecision && rules.find((x) => x.rule.id === String(focusDecision.payload.rule_id));
  const focusAt = focusDecision ? Number(focusDecision.payload.at_ns) : horizon / 2;
  const win = focusRule?.rule.window_ns ?? 600 * NS;
  const d0 = zoom ? Math.max(0, focusAt - win) : 0;
  const d1 = zoom ? Math.min(horizon, focusAt + win / 2) : horizon;

  const kindsPerRole = new Map<string, string[]>();
  for (const r of roles) {
    const ks = new Set(trace.views[r].events.map((e) => e.kind).filter((k) => shown.has(k)));
    if (showCompare) for (const e of root.trace?.views[r]?.events ?? []) if (shown.has(e.kind)) ks.add(e.kind);
    const kinds = orderKinds(ks).filter((k) => k !== "decision");
    if (trace.decisions.some((d) => roleOfDecision(trace, d) === r)) kinds.push("decision");
    kindsPerRole.set(r, kinds);
  }
  const laneH = (r: string) => Math.max(LANE_MIN, LANE_PAD * 2 + kindsPerRole.get(r)!.length * SUBROW);
  const laneTop = new Map<string, number>();
  let y = AXIS_H;
  for (const r of roles) {
    laneTop.set(r, y);
    y += laneH(r) + 8;
  }
  const height = y + 14;
  const plotW = width - LEFT - RIGHT;
  const X = (ns: number) => LEFT + ((Math.max(d0, Math.min(d1, ns)) - d0) / (d1 - d0)) * plotW;
  const inDomain = (a: number, b: number) => Math.max(a, b) >= d0 && Math.min(a, b) <= d1;
  const Y = (r: string, kind: string) => laneTop.get(r)! + LANE_PAD + Math.max(0, kindsPerRole.get(r)!.indexOf(kind)) * SUBROW + SUBROW / 2;
  const span = d1 - d0;
  const tickStep = span > 2400 * NS ? 300 * NS : span > 900 * NS ? 120 * NS : 60 * NS;
  const ticks: number[] = [];
  for (let tk = Math.ceil(d0 / tickStep) * tickStep; tk <= d1; tk += tickStep) ticks.push(tk);

  const tKnown = t ?? horizon;
  const arrivals = [...new Set(roles.flatMap((r) => trace.views[r].events.filter((e) => shown.has(e.kind)).map((e) => e.received_time_ns ?? e.event_time_ns)))].sort((a, b) => a - b);
  const knownCount = (r: string) => trace.views[r].events.filter((e) => (e.received_time_ns ?? e.event_time_ns) <= tKnown).length;
  const stepTo = (dir: 1 | -1) => {
    const cur = tKnown;
    const next = dir === 1 ? arrivals.find((a) => a > cur) : [...arrivals].reverse().find((a) => a < cur);
    if (next != null) setT(next);
  };

  const rootView = (role: string) => new Map((showCompare ? root.trace?.views[role]?.events ?? [] : []).map((e) => [e.id, e]));
  const rootDecisions = new Map((showCompare ? root.trace?.decisions ?? [] : []).map((d) => [String(d.payload.rule_id), d]));
  const isWit = (e: WEvent) => witness.has(e.id) || witness.has(e.id.split("#")[0]);

  // ------------------------------------------------------------ the one-sentence answer
  const focusRole = focusDecision ? roleOfDecision(trace, focusDecision) : roles[0];
  const answer = blamed ? (
    <>
      At <b>{mmss(focusAt)}</b> the {humanName(blamed.actor ?? "decider")} ({humanName(focusRole)}) chose <b className="bad">{blamed.chose}</b>, but the truth called for <b>{blamed.truth}</b>: it had not yet heard{" "}
      {blamed.missing.length ? (
        <>
          {blamed.missing.slice(0, 3).map((m, i) => (
            <span key={m}>
              {i > 0 && (i === Math.min(3, blamed.missing.length) - 1 ? " and " : ", ")}
              <code className="wit">{m}</code>
            </span>
          ))}
          {blamed.missing.length > 3 && ` and ${blamed.missing.length - 3} more`}
        </>
      ) : (
        "what it needed"
      )}
      .
    </>
  ) : (
    <>
      All {trace.decisions.length} decision{trace.decisions.length === 1 ? "" : "s"} matched the truth: each role decided on what it had received, and that was enough. Drag the time bar to see what each role knew at any moment.
    </>
  );

  const hoverRoot = hover && showCompare ? rootView(hover.role).get(hover.event.id) : undefined;

  return (
    <div className="view ix-view">
      {intro(answer)}
      {head}

      <section className="panel ix-tl-panel" aria-label="Role timelines">
        <div className="ix-tl-controls">
          <div className="ix-scrub" role="group" aria-label="Known-at time">
            <span className="ix-scrub-label">
              Known at <b className="ix-mono">{mmss(tKnown)}</b>
              {t === null && <span className="muted"> (end)</span>}
            </span>
            <button className="btn btn-sm btn-icon-sm" onClick={() => stepTo(-1)} aria-label="Previous arrival" title="Previous arrival">
              ‹
            </button>
            <input
              type="range"
              min={0}
              max={horizon}
              step={5 * NS}
              value={tKnown}
              onChange={(e) => setT(Number(e.target.value))}
              aria-label="Known-at time"
              aria-valuetext={`${mmss(tKnown)} into the scenario; ${roles.map((r) => `${humanName(r)} knows ${knownCount(r)} events`).join(", ")}`}
            />
            <button className="btn btn-sm btn-icon-sm" onClick={() => stepTo(1)} aria-label="Next arrival" title="Next arrival">
              ›
            </button>
            {focusDecision && (
              <button className="btn btn-sm" onClick={() => setT(focusAt)} title="Set the time bar to the moment of the decision">
                At the decision
              </button>
            )}
            <button className="btn btn-sm btn-ghost" onClick={() => setT(null)} disabled={t === null}>
              Reset
            </button>
          </div>
          <button className={`btn btn-sm ${zoom ? "btn-primary" : ""}`} aria-pressed={zoom} onClick={() => setZoom(!zoom)} disabled={!focusDecision} title={focusRule ? `Rule ${focusRule.rule.id} looks back ${humanDuration(win)} from ${mmss(focusAt)}` : ""}>
            {zoom ? "Show the whole hour" : "Zoom to the decision window"}
          </button>
        </div>

        <div className="ix-kinds" role="group" aria-label="Event kinds shown">
          <span className="ix-filter-k">Show</span>
          <Chip on={preset === "relevant"} count={relevant.size} onClick={() => setPreset("relevant")} title="Kinds a decision rule reads, kinds in the FAIL witness, and kinds the fault schedule touches">
            Decision-relevant
          </Chip>
          <Chip on={preset === "all"} count={allKinds.size} onClick={() => setPreset("all")}>
            All kinds
          </Chip>
          <span className="ix-sep" aria-hidden />
          {orderKinds(allKinds.keys()).map((k) => (
            <Chip
              key={k}
              on={shown.has(k)}
              count={allKinds.get(k)}
              onClick={() => {
                const n = new Set(shown);
                if (n.has(k)) n.delete(k);
                else n.add(k);
                setCustom(n);
                setPreset("custom");
              }}
            >
              {kindWord(k)}
            </Chip>
          ))}
        </div>

        <div className="ix-legend" aria-label="How to read the lanes">
          <span>
            <svg width="30" height="12" aria-hidden>
              <circle cx="5" cy="6" r="3.5" className="ev-mark" />
              <line x1="8" y1="6" x2="25" y2="6" className="lag-line" />
              <line x1="25" y1="2" x2="25" y2="10" className="recv-tick" />
            </svg>
            happened → heard about it
          </span>
          <span>
            <svg width="14" height="14" aria-hidden>
              <path d="M7 1L13 7 7 13 1 7z" className="dec-mark" />
            </svg>
            decision
          </span>
          <span>
            <svg width="16" height="16" aria-hidden>
              <circle cx="8" cy="8" r="6" className="wit-ring ix-wit-ring" />
              <circle cx="8" cy="8" r="3" fill="var(--fail)" />
            </svg>
            witness: what the decider was missing
          </span>
          {showCompare && (
            <span>
              <svg width="30" height="12" aria-hidden>
                <circle cx="5" cy="6" r="3.5" className="root-mark" />
                <line x1="8" y1="6" x2="25" y2="6" className="delta-line" />
              </svg>
              baseline arrival → this world's arrival
            </span>
          )}
          <span className="muted">Hover or focus a mark for details; click to pin it.</span>
        </div>

        {diff && (
          <p className="ix-diff-sentence" role="status">
            <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden>
              <path d="M2 5h9M8 2l3 3-3 3M14 11H5M8 8l-3 3 3 3" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            {diffSummary(diff)}
          </p>
        )}

        <div ref={wrapRef} className="ix-tl-wrap" style={{ height }}>
          <svg className="tl" width={width} height={height} role="group" aria-label={`Timelines for ${roles.length} roles from ${mmss(d0)} to ${mmss(d1)}`}>
            {focusRule && (
              <rect x={X(focusAt - win)} y={AXIS_H - 6} width={Math.max(0, X(focusAt) - X(focusAt - win))} height={height - AXIS_H} className="ix-window" />
            )}
            {ticks.map((tk) => (
              <g key={tk} transform={`translate(${X(tk)},0)`}>
                <line y1={AXIS_H - 6} y2={height} className="grid" />
                <text y={AXIS_H - 12} className="axis-label" textAnchor="middle">
                  {mmss(tk)}
                </text>
              </g>
            ))}
            {tKnown < d1 && <rect x={X(tKnown)} y={AXIS_H - 6} width={Math.max(0, width - RIGHT - X(tKnown))} height={height - AXIS_H} className="ix-future" />}
            {roles.map((r) => {
              const top = laneTop.get(r)!;
              const h = laneH(r);
              const view = trace.views[r].events.filter((e) => shown.has(e.kind));
              const rv = showCompare ? rootView(r) : null;
              return (
                <g key={r} className="lane">
                  <rect x={0} y={top} width={width} height={h} className="lane-bg" rx={8} />
                  {kindsPerRole.get(r)!.map((k) => (
                    <text key={k} x={LEFT - 10} y={Y(r, k) + 4} className="kind-label" textAnchor="end">
                      {k === "decision" ? "decision" : kindWord(k)}
                    </text>
                  ))}
                  {rv &&
                    view.map((e) => {
                      const o = rv.get(e.id);
                      if (!o || o.received_time_ns == null || e.received_time_ns == null || o.received_time_ns === e.received_time_ns || !inDomain(o.received_time_ns, e.received_time_ns)) return null;
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
                      .filter((o) => shown.has(o.kind) && !trace.views[r].events.some((e) => e.id === o.id) && inDomain(o.event_time_ns, o.event_time_ns))
                      .map((o) => (
                        <g key={`only-root-${o.id}`} className="overlay only-root">
                          <circle cx={X(o.event_time_ns)} cy={Y(r, o.kind)} r={4} className="root-mark" />
                          <text x={X(o.event_time_ns) + 7} y={Y(r, o.kind) - 6} className="tiny">
                            {o.id} never arrived here
                          </text>
                        </g>
                      ))}
                  {view.map((e, idx) => {
                    const recv = e.received_time_ns ?? e.event_time_ns;
                    if (!inDomain(e.event_time_ns, recv)) return null;
                    const yy = Y(r, e.kind);
                    const known = recv <= tKnown;
                    const wit = isWit(e);
                    const isPicked = picked?.event === e && picked.role === r;
                    const open = (ev: React.MouseEvent | React.FocusEvent) => {
                      const rect = (ev.currentTarget as SVGGElement).getBoundingClientRect();
                      const pt = "clientX" in ev ? { x: ev.clientX, y: ev.clientY } : { x: rect.right, y: rect.bottom };
                      setHover({ role: r, event: e, ...pt });
                    };
                    return (
                      <g
                        key={`${e.id}-${idx}`}
                        className={`ev ${known ? "" : "ev-unknown"} ${wit ? "ev-witness ix-ev-witness" : ""} ${isPicked ? "ev-picked" : ""} ev-kind-${e.kind}`}
                        onClick={() => setPicked({ role: r, event: e })}
                        onMouseEnter={open}
                        onMouseMove={open}
                        onMouseLeave={() => setHover(null)}
                        onFocus={open}
                        onBlur={() => setHover(null)}
                        onKeyDown={(k) => (k.key === "Enter" || k.key === " ") && (k.preventDefault(), setPicked({ role: r, event: e }))}
                        role="button"
                        tabIndex={wit ? 0 : -1}
                        aria-label={`${kindWord(e.kind)} ${e.id} for ${humanName(r)}: ${eventSummary(e.kind, e.payload, e.source_id)} Happened ${mmss(e.event_time_ns)}, heard ${mmss(recv)}.${wit ? " Witness event." : ""}`}
                      >
                        <rect x={Math.min(X(e.event_time_ns), X(recv)) - 6} y={yy - 7} width={Math.abs(X(recv) - X(e.event_time_ns)) + 12} height={14} className="ix-hit" />
                        <line x1={X(e.event_time_ns)} x2={X(recv)} y1={yy} y2={yy} className="lag-line" />
                        <line x1={X(recv)} x2={X(recv)} y1={yy - 4} y2={yy + 4} className="recv-tick" />
                        {wit && <circle cx={X(e.event_time_ns)} cy={yy} r={8} className="wit-ring ix-wit-ring" />}
                        <circle cx={X(e.event_time_ns)} cy={yy} r={wit ? 4.5 : 3.5} className="ev-mark" />
                        {wit && (
                          <text x={X(recv) + 6} y={yy - 6} className="ix-wit-label">
                            {e.id}
                          </text>
                        )}
                      </g>
                    );
                  })}
                  {trace.decisions
                    .filter((d) => roleOfDecision(trace, d) === r)
                    .map((d) => {
                      const at = Number(d.payload.at_ns);
                      if (!inDomain(at, at)) return null;
                      const yy = Y(r, "decision");
                      const rd = showCompare ? rootDecisions.get(String(d.payload.rule_id)) : undefined;
                      const differs = rd && rd.payload.option !== d.payload.option;
                      const blamedHere = blamed && (d.id === blamed.decisionId || String(d.payload.actor) === blamed.actor);
                      const isPicked = picked?.event.id === d.id;
                      const open = (ev: React.MouseEvent | React.FocusEvent) => {
                        const rect = (ev.currentTarget as SVGGElement).getBoundingClientRect();
                        const pt = "clientX" in ev ? { x: ev.clientX, y: ev.clientY } : { x: rect.right, y: rect.bottom };
                        setHover({ role: r, event: d, ...pt });
                      };
                      return (
                        <g
                          key={d.id}
                          className={`dec ${at <= tKnown ? "" : "ev-unknown"} ${differs || blamedHere ? "dec-differs" : ""} ${isPicked ? "ev-picked" : ""}`}
                          onClick={() => setPicked({ role: r, event: d })}
                          onMouseEnter={open}
                          onMouseLeave={() => setHover(null)}
                          onFocus={open}
                          onBlur={() => setHover(null)}
                          onKeyDown={(k) => (k.key === "Enter" || k.key === " ") && (k.preventDefault(), setPicked({ role: r, event: d }))}
                          role="button"
                          tabIndex={0}
                          aria-label={`${humanName(String(d.payload.rule_id))} → ${String(d.payload.option)}${differs ? ` (baseline: ${String(rd!.payload.option)})` : ""}: decision ${String(d.payload.rule_id)} at ${mmss(at)} by ${humanName(String(d.payload.actor))}`}
                        >
                          <line x1={X(at)} x2={X(at)} y1={top} y2={top + h} className="dec-line" />
                          <rect x={X(at) - 9} y={yy - 9} width={18} height={18} className="ix-hit" />
                          <path d={`M${X(at)} ${yy - 7}L${X(at) + 7} ${yy}L${X(at)} ${yy + 7}L${X(at) - 7} ${yy}z`} className="dec-mark" />
                          <text x={X(at) + (X(at) > width - 260 ? -11 : 11)} y={yy + 4} className="dec-label" textAnchor={X(at) > width - 260 ? "end" : "start"}>
                            {humanName(String(d.payload.rule_id))} → <tspan className="dec-opt">{String(d.payload.option)}</tspan>
                            {differs && <tspan className="dec-root"> (baseline: {String(rd!.payload.option)})</tspan>}
                          </text>
                        </g>
                      );
                    })}
                </g>
              );
            })}
            {tKnown <= d1 && tKnown >= d0 && (
              <g transform={`translate(${X(tKnown)},0)`} className="scrub-line">
                <line y1={AXIS_H - 6} y2={height - 12} />
                <text y={height - 2} x={X(tKnown) > width - 110 ? -4 : 4} textAnchor={X(tKnown) > width - 110 ? "end" : "start"} className="tiny">
                  known at {mmss(tKnown)}
                </text>
              </g>
            )}
          </svg>
          {roles.map((r) => (
            <LaneHead key={r} role={r} trace={trace} top={laneTop.get(r)!} height={laneH(r)} known={knownCount(r)} total={trace.views[r].events.length} />
          ))}
        </div>
        {zoom && (
          <p className="small muted ix-zoom-note">
            Showing {mmss(d0)}–{mmss(d1)}: the {humanDuration(win)} the rule <code>{focusRule?.rule.id}</code> looks back over (shaded), plus what arrived just after.
          </p>
        )}
      </section>

      {hover && (
        <HoverCard x={hover.x} y={hover.y}>
          <EventSummary role={hover.role} e={hover.event} witness={isWit(hover.event)} tKnown={tKnown} rootEvent={hoverRoot} />
        </HoverCard>
      )}

      <div className="ix-split ix-split-even">
        <section className="panel" aria-label="Pinned event">
          <h2 className="ix-h2">{picked ? "Pinned event" : "Pin an event"}</h2>
          {picked ? (
            <>
              <EventSummary role={picked.role} e={picked.event} witness={isWit(picked.event)} tKnown={tKnown} rootEvent={showCompare ? rootView(picked.role).get(picked.event.id) : undefined} />
              <Disclosure summary="Raw event">
                <dl className="kv">
                  <dt>id</dt>
                  <dd>
                    <code>{picked.event.id}</code>
                  </dd>
                  <dt>event_time_ns</dt>
                  <dd>{picked.event.event_time_ns.toLocaleString()}</dd>
                  <dt>received_time_ns</dt>
                  <dd>{(picked.event.received_time_ns ?? picked.event.event_time_ns).toLocaleString()}</dd>
                  {picked.event.policy_labels.length > 0 && (
                    <>
                      <dt>policy_labels</dt>
                      <dd>{picked.event.policy_labels.join(", ")}</dd>
                    </>
                  )}
                  {picked.event.provenance_ids.length > 0 && (
                    <>
                      <dt>provenance_ids</dt>
                      <dd>{picked.event.provenance_ids.join(", ")}</dd>
                    </>
                  )}
                </dl>
                <pre className="json">{JSON.stringify(picked.event.payload, null, 2)}</pre>
              </Disclosure>
            </>
          ) : (
            <p className="small muted">Click any mark (or focus a witness or decision with Tab and press Enter) to keep its details here while you move the time bar.</p>
          )}
        </section>

        <section className="panel" aria-label="This world">
          <h2 className="ix-h2">This world</h2>
          {entry?.schedule && entry.schedule.length > 0 ? (
            <ol className="ix-changes ix-changes-lg">
              {entry.schedule.map((iv, k) => (
                <li key={k}>
                  <span className={`ix-op op-${iv.op}`}>{opName(iv.op)}</span> {interventionPlain(iv)}
                </li>
              ))}
            </ol>
          ) : (
            <p className="small muted">{trace.branch_id === "root" ? "Nothing changed: the recorded hour as it happened." : "The fault schedule is not recorded for this trace."}</p>
          )}
          <ul className="ix-verdicts">
            {verdicts
              .filter((v) => v.result !== "PASS")
              .map((v) => (
                <li key={v.invariant_id} className={`ix-verdict v-${v.result.toLowerCase()}`}>
                  <div className="ix-verdict-head">
                    <VerdictPill v={v.result} /> <b>{invariantName(v.invariant_id)}</b> <Tip text={invariantHelp(v.invariant_id)} label={`About ${v.invariant_id}`} />
                  </div>
                  <code className="ix-term">{v.invariant_id}</code>
                  <p className="small" title={v.explanation}>
                    {verdictSentence(v)}
                  </p>
                </li>
              ))}
          </ul>
          {verdicts.some((v) => v.result === "PASS") && (
            <Disclosure summary={`${verdicts.filter((v) => v.result === "PASS").length} rules held`}>
              <ul className="ix-checklist">
                {verdicts
                  .filter((v) => v.result === "PASS")
                  .map((v) => (
                    <Check key={v.invariant_id} ok label={invariantName(v.invariant_id)} term={v.invariant_id} help={v.explanation} />
                  ))}
              </ul>
            </Disclosure>
          )}
          {verdicts.length === 0 && <p className="small muted">No verdict file for this trace.</p>}
          <Disclosure summary="Identifiers">
            <div className="ix-ids">
              {trace.branch_id !== "root" && <ShortId value={trace.branch_id} label="branch" />}
              <ShortId value={trace.digest} label="trace" />
              <span className="small muted">seed {trace.seed}</span>
            </div>
          </Disclosure>
        </section>
      </div>

      {diff && (
        <Disclosure summary="Every difference from the baseline" meta={`${diff.decisions.length} decision${diff.decisions.length === 1 ? "" : "s"} · ${diff.moved.length} arrival${diff.moved.length === 1 ? "" : "s"} moved`} className="panel-disc">
          <h5>Decisions that differ</h5>
          {diff.decisions.length === 0 && <div className="muted small">none</div>}
          <ul className="plain ix-plain">
            {diff.decisions.map((d) => (
              <li key={d.rule_id} className="small">
                <b>{humanName(d.rule_id)}</b> at {mmss(d.at_ns)}: <code>{d.option_a ?? "none"}</code> → <code className="bad">{d.option_b ?? "none"}</code>
                {d.known_only_a.length > 0 && <div className="muted">known only in the baseline: {d.known_only_a.join(", ")}</div>}
                {d.known_only_b.length > 0 && <div className="muted">known only in this world: {d.known_only_b.join(", ")}</div>}
              </li>
            ))}
          </ul>
          <h5>Arrivals that moved</h5>
          {diff.moved.length === 0 && <div className="muted small">none</div>}
          <ul className="plain ix-plain">
            {diff.moved.map((m) => (
              <li key={`${m.role}/${m.id}`} className="small">
                <code>{m.id}</code> for <b>{humanName(m.role)}</b>: {mmss(m.from_ns)} → {mmss(m.to_ns)}{" "}
                <span className={m.delta_ns > 0 ? "bad" : ""}>
                  ({m.delta_ns > 0 ? "+" : "−"}
                  {humanDuration(m.delta_ns)})
                </span>
              </li>
            ))}
          </ul>
          {(diff.only_in_a.length > 0 || diff.only_in_b.length > 0 || diff.corrupted.length > 0) && (
            <>
              <h5>Presence and content</h5>
              <ul className="plain ix-plain">
                {diff.only_in_a.map((x) => (
                  <li key={`a-${x.role}-${x.event.id}`} className="small">
                    <code>{x.event.id}</code> never reached <b>{humanName(x.role)}</b> in this world
                  </li>
                ))}
                {diff.only_in_b.map((x) => (
                  <li key={`b-${x.role}-${x.event.id}`} className="small">
                    <code>{x.event.id}</code> reached <b>{humanName(x.role)}</b> only in this world
                  </li>
                ))}
                {diff.corrupted.map((c) => (
                  <li key={`${c.role}/${c.id}`} className="small">
                    <code>{c.id}</code> arrived at <b>{humanName(c.role)}</b> with different content
                  </li>
                ))}
              </ul>
            </>
          )}
        </Disclosure>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- pieces

function LaneHead({ role, trace, top, height, known, total }: { role: string; trace: Trace; top: number; height: number; known: number; total: number }) {
  const spec = trace.scenario?.roles?.find((r) => r.id === role);
  const actors = (trace.scenario?.actors ?? []).filter((a) => a.role === role);
  const sees = !spec ? "" : spec.visibility.includes("*") ? "sees everything" : `sees ${spec.visibility.map(humanName).join(" + ")}`;
  const delay = !spec ? "" : spec.delivery.base_latency_ns === 0 && spec.delivery.jitter_ns === 0 ? "No delay" : `~${secs(spec.delivery.base_latency_ns)} late${spec.delivery.ordering !== "fifo" ? ", out of order" : ""}`;
  const isEvaluator = role === "evaluator";
  const help = (
    <>
      <b>{role}</b>
      {spec && (
        <>
          <br />
          delivery: base latency {secs(spec.delivery.base_latency_ns)}, jitter {secs(spec.delivery.jitter_ns)}, ordering {spec.delivery.ordering}, drop probability {spec.delivery.drop_probability}
          <br />
          visibility: {spec.visibility.join(", ")}
        </>
      )}
      {actors.map((a) =>
        a.decision_rules.map((rule) => (
          <span key={rule.id}>
            <br />
            {a.id} decides <code>{rule.id}</code> at {mmss(rule.at_ns)}: if {rule.when} then {rule.then}, else {rule.otherwise}
          </span>
        )),
      )}
    </>
  );
  return (
    <div className="ix-lane-head" style={{ top, height }}>
      <div className="ix-lane-title">
        {humanName(role)}
        <Tip text={help} label={`About the ${humanName(role)} role`} />
      </div>
      {actors.some((a) => a.decision_rules.length) ? (
        <div className="ix-lane-desc">
          {actors.flatMap((a) => a.decision_rules.map((rule) => `Decides at ${mmss(rule.at_ns)}: ${humanName(rule.id)}`)).join("; ")}
        </div>
      ) : isEvaluator ? (
        <div className="ix-lane-desc">Observer the checks use as ground truth</div>
      ) : null}
      <div className="ix-lane-desc muted">
        {delay}
        {sees && delay ? " · " : ""}
        {sees}
      </div>
      <div className="ix-lane-known" aria-label={`${known} of ${total} events known`}>
        knows {known}/{total}
      </div>
    </div>
  );
}

function EventSummary({ role, e, witness, tKnown, rootEvent }: { role: string; e: WEvent; witness: boolean; tKnown: number; rootEvent?: WEvent }) {
  const isDecision = e.kind === "decision";
  const recv = e.received_time_ns ?? e.event_time_ns;
  const lag = recv - e.event_time_ns;
  const rootRecv = rootEvent?.received_time_ns;
  return (
    <div className="ix-ev">
      <div className="ix-ev-head">
        {isDecision ? <span className="ix-kind-tag is-dec">decision</span> : <span className="ix-kind-tag">{kindWord(e.kind)}</span>}
        <code>{e.id}</code>
        {witness && (
          <span className="pill pill-fail">
            <VerdictGlyph v="FAIL" size={9} /> witness
          </span>
        )}
      </div>
      <p className="ix-ev-sentence">{eventSummary(e.kind, e.payload, e.source_id)}</p>
      {isDecision ? (
        <p className="small muted">
          Decided at {mmss(Number(e.payload.at_ns))} by {humanName(String(e.payload.actor))} ({humanName(role)}).
        </p>
      ) : (
        <p className="small">
          Happened at <b className="ix-mono">{mmss(e.event_time_ns)}</b>; {humanName(role)} heard about it at <b className="ix-mono">{mmss(recv)}</b>
          {lag > 0 ? <span className="muted"> ({humanDuration(lag)} later)</span> : null}.
          {rootRecv != null && rootRecv !== recv && (
            <>
              {" "}
              In the baseline it arrived at <span className="ix-mono">{mmss(rootRecv)}</span>{" "}
              <span className={recv > rootRecv ? "bad" : ""}>
                ({recv > rootRecv ? "+" : "−"}
                {humanDuration(recv - rootRecv)})
              </span>
              .
            </>
          )}
        </p>
      )}
      {witness && <p className="small ix-ev-wit">The failing check names this event: the decider had not received it when it decided.</p>}
      {!isDecision && recv > tKnown && <p className="small muted">Not yet heard at {mmss(tKnown)} on the time bar.</p>}
    </div>
  );
}

function TraceContext({
  selected,
  registry,
  onSelect,
  trace,
  compare,
  canCompare,
  setCompare,
  rootBranch,
}: {
  selected: string;
  registry: Map<string, TraceEntry>;
  onSelect: (id: string) => void;
  trace: Trace | null;
  compare: boolean;
  canCompare: boolean;
  setCompare: (b: boolean) => void;
  rootBranch: boolean;
}) {
  const groups = GROUP_ORDER.map((g) => ({ g, entries: [...registry.values()].filter((e) => e.group === g) })).filter((x) => x.entries.length);
  const known = registry.has(selected);
  const entry = registry.get(selected);
  return (
    <ContextBar label="Timeline context">
      <label className="ix-select">
        <span className="ix-ctx-k">World</span>
        <select value={known ? selected : ""} onChange={(e) => onSelect(e.target.value)}>
          {!known && <option value="">{shortBranch(selected)} (no timeline)</option>}
          {groups.map(({ g, entries }) => (
            <optgroup key={g} label={GROUP_LABEL[g]}>
              {entries.map((e) => (
                <option key={e.key} value={e.key}>
                  {g === "guided" || g === "random" || g === "windowed" ? `${shortBranch(e.key).replace("root/", "")} · ${(e.failingInvariants ?? []).map(invariantName).join(", ")}` : g === "root" ? "Baseline (nothing changed)" : g === "minimal" ? "Minimal counterexample (one change)" : `Challenge: ${e.scenarioId}`}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </label>
      {entry && (
        <Ctx k="Scenario">
          <code>{entry.scenarioId}</code>
          {trace && <span className="muted"> seed {trace.seed}</span>}
        </Ctx>
      )}
      <span className="ix-context-spacer" />
      <label className={`ix-switch ${canCompare ? "" : "disabled"}`} title={rootBranch ? "This is the baseline; pick another world to compare" : canCompare ? "Overlay the baseline's arrival times and summarise what changed" : "Different scenario: there is no baseline to compare with"}>
        <input type="checkbox" role="switch" checked={compare && canCompare} disabled={!canCompare} onChange={(e) => setCompare(e.target.checked)} />
        <span className="ix-switch-track" aria-hidden>
          <span className="ix-switch-thumb" />
        </span>
        Compare with the baseline
      </label>
    </ContextBar>
  );
}

