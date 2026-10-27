import { useEffect, useMemo, useRef, useState } from "react";
import type { Authorization, BranchNode, ChallengeResult, EvidenceKind, EvidenceRecord, ExplorationRun, LoadedData, Trace, WEvent } from "../types";
import { dataUrl, useTrace, challengeKey, type TraceEntry } from "../data";
import { eventWord, humanDuration, humanName, interventionSentence, interventionText, mmss, nodeStatus, parseDecisionExplanation, preconditionText, NS } from "../format";
import { KIND_HELP, KIND_LABEL, KindGlyph, VerdictGlyph } from "../components/Badges";
import { ShortId } from "../components/ShortId";
import { AUTHORIZE_ENDPOINT, DEFAULT_POLICY, HEALTH_ENDPOINT, MAX_BUNDLE_BYTES, RELEASE_TAG, RELEASE_URL, STORY_BEATS, VERIFIER_DOCS_URL, VERIFIER_URL, VERIFY_ENDPOINT, MERGE_REQUEST_URL } from "../config";
import Landing from "./Landing";

type InspectView = "worlds" | "timeline" | "mechanism" | "evidence";

interface Props {
  data: LoadedData;
  registry: Map<string, TraceEntry>;
  onInspect: (v: InspectView, branch?: string) => void;
}

function useWidth<T extends HTMLElement>(initial = 960): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(initial);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    setW(Math.floor(el.getBoundingClientRect().width));
    const ro = new ResizeObserver((entries) => setW(Math.floor(entries[0].contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

export default function Story({ data, registry, onInspect }: Props) {
  const ex = data.exploration;
  const guided = ex.runs.guided ?? Object.values(ex.runs)[0];
  const totalWorlds = Object.values(ex.runs).reduce((a, r) => a + r.nodes.filter((n) => n.branch_id !== "root").length, 0);
  const totalFailing = Object.values(ex.runs).reduce((a, r) => a + r.n_failing, 0);

  // Deep link: #/story/<n> scrolls to beat n once the page has laid out.
  useEffect(() => {
    const m = location.hash.match(/^#\/story\/(\d)/);
    if (!m) return;
    const jump = () => document.getElementById(`beat-${m[1]}`)?.scrollIntoView({ block: "start" });
    const t1 = setTimeout(jump, 250);
    const t2 = setTimeout(jump, 1200);
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
    };
  }, []);

  return (
    <div className="story">
      <Landing data={data} totalWorlds={totalWorlds} totalFailing={totalFailing} />
      <BeatWorlds data={data} defaultRun={guided} totalWorlds={totalWorlds} totalFailing={totalFailing} onInspect={onInspect} />
      <BeatTimeline data={data} registry={registry} onInspect={onInspect} />
      <BeatOneCause data={data} onInspect={onInspect} />
      <BeatGenome data={data} onInspect={onInspect} />
      <BeatChallenge data={data} registry={registry} onInspect={onInspect} />
      <BeatEvidence data={data} onInspect={onInspect} />
      <BeatVerify data={data} />
    </div>
  );
}

// ------------------------------------------------------------------ shell

function Beat({ n, title = STORY_BEATS[n - 1].title, lede, layout = "stack", aside, inspect, children }: { n: number; title?: string; lede: React.ReactNode; layout?: "stack" | "split"; aside?: React.ReactNode; inspect?: { label: string; onClick: () => void }; children: React.ReactNode }) {
  return (
    <section className={`beat beat-${layout}`} id={`beat-${n}`} aria-labelledby={`beat-${n}-h`}>
      <div className="beat-in">
        <header className="beat-head">
          <span className="beat-num">
            <span className="beat-num-n">{String(n).padStart(2, "0")}</span>
            <span className="beat-num-of">/ {String(STORY_BEATS.length).padStart(2, "0")}</span>
            <span className="beat-num-label">{STORY_BEATS[n - 1].short}</span>
          </span>
          <h2 id={`beat-${n}-h`}>{title}</h2>
          <p className="beat-lede">{lede}</p>
          {aside}
          {inspect && (
            <button type="button" className="inspect-link" onClick={inspect.onClick}>
              {inspect.label}
              <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden>
                <path d="M3 8h9M8.5 4.5L12 8l-3.5 3.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          )}
        </header>
        <div className="beat-visual">{children}</div>
      </div>
    </section>
  );
}

// ------------------------------------------------------------------ 1. worlds

function BeatWorlds({ data, defaultRun, totalWorlds, totalFailing, onInspect }: { data: LoadedData; defaultRun: ExplorationRun; totalWorlds: number; totalFailing: number; onInspect: Props["onInspect"] }) {
  const ex = data.exploration;
  const strategies = ex.strategies.filter((s) => ex.runs[s]);
  const [strategy, setStrategy] = useState(defaultRun.strategy);
  const run = ex.runs[strategy] ?? defaultRun;
  const g = ex.comparison.guided;
  const r = ex.comparison.random;
  return (
    <Beat
      n={1}
      title={`One hour, replayed ${totalWorlds} ways`}
      layout="split"
      lede={
        <>
          WORLDLINE takes one recorded hour of a harbour alert and replays it {totalWorlds} times, each time shifting when a few messages arrive. Each replay is a <em>world</em>. In {totalFailing} of them a safety rule broke; each of those is a red diamond.
        </>
      }
      aside={g && r ? <GuidedVsRandom g={g} r={r} /> : null}
      inspect={{ label: "Inspect all worlds", onClick: () => onInspect("worlds", data.minimal.branch_id) }}
    >
      <div className="viz-card">
        <div className="viz-toolbar">
          <div className="seg" role="tablist" aria-label="Search strategy">
            {strategies.map((s) => (
              <button type="button" key={s} role="tab" aria-selected={s === strategy} className={`seg-btn ${s === strategy ? "active" : ""}`} onClick={() => setStrategy(s)}>
                {s.charAt(0).toUpperCase() + s.slice(1)}
              </button>
            ))}
          </div>
          <VerdictLegend run={run} />
        </div>
        <FanTree run={run} highlight={data.minimal.branch_id} onPick={(id) => onInspect("worlds", id)} />
      </div>
    </Beat>
  );
}

function GuidedVsRandom({ g, r }: { g: LoadedData["exploration"]["comparison"][string]; r: LoadedData["exploration"]["comparison"][string] }) {
  const max = Math.max(g.failing_rate_per_100, r.failing_rate_per_100, 1);
  return (
    <div className="gvr" aria-label="Guided versus random search">
      <div className="gvr-nums">
        <div>
          <div className="big-num">{g.n_failing}</div>
          <div className="big-label">
            failing worlds, <b>guided search</b>
          </div>
        </div>
        <div>
          <div className="big-num big-num-muted">{r.n_failing}</div>
          <div className="big-label">failing worlds, random search</div>
        </div>
      </div>
      <div className="minibars" role="table" aria-label="Failing worlds per 100 tried">
        <div className="mb-caption">per 100 worlds tried</div>
        {[
          ["Guided", g.failing_rate_per_100, true],
          ["Random", r.failing_rate_per_100, false],
        ].map(([label, v, hl]) => (
          <div className="mb-row" role="row" key={String(label)} title={`${label}: ${(v as number).toFixed(1)} failing worlds per 100`}>
            <span className="mb-label" role="cell">
              {label as string}
            </span>
            <span className="mb-track" role="cell">
              <span className={`mb-bar ${hl ? "mb-bar-hl" : ""}`} style={{ width: `${((v as number) / max) * 100}%` }} />
            </span>
            <span className="mb-val" role="cell">
              {(v as number).toFixed(1)}
            </span>
          </div>
        ))}
      </div>
      <p className="gvr-note">
        <b>Guided search</b> aims its timing changes at the message types a decision depends on; random search picks any message.{" "}
        {g.first_decision_fail_index != null ? (
          <>
            Guided found its first <b>wrong decision</b> in world #{g.first_decision_fail_index}; random {r.first_decision_fail_index == null ? "never did within the same budget" : `took until world #${r.first_decision_fail_index}`}.
          </>
        ) : (
          <>Neither strategy produced a wrong decision within budget.</>
        )}
      </p>
    </div>
  );
}

function VerdictLegend({ run }: { run: ExplorationRun }) {
  const c = { PASS: 0, FAIL: 0, UNKNOWN: 0 };
  for (const n of run.nodes) if (n.branch_id !== "root") c[nodeStatus(n)]++;
  return (
    <div className="legend" aria-label="Legend">
      <span>
        <VerdictGlyph v="FAIL" /> broke a rule <b>{c.FAIL}</b>
      </span>
      <span>
        <VerdictGlyph v="PASS" /> held <b>{c.PASS}</b>
      </span>
      <span>
        <VerdictGlyph v="UNKNOWN" /> unknown <b>{c.UNKNOWN}</b>
      </span>
    </div>
  );
}

/** Half-circle fan: the real hour at the bottom centre, every explored world on the arc in exploration order. */
function FanTree({ run, highlight, onPick }: { run: ExplorationRun; highlight: string; onPick: (id: string) => void }) {
  const W = 640;
  const H = 352;
  const cx = W / 2;
  const cy = H - 34;
  const R = 278;
  const kids = run.nodes.filter((n) => n.branch_id !== "root");
  const [hover, setHover] = useState<{ n: BranchNode; x: number; y: number } | null>(null);
  const pts = kids.map((n, i) => {
    const a = Math.PI - (Math.PI * (i + 0.5)) / kids.length;
    return { n, x: cx + R * Math.cos(a), y: cy - R * Math.sin(a), st: nodeStatus(n) };
  });
  const order = [...pts].sort((a, b) => (a.st === "FAIL" ? 1 : 0) - (b.st === "FAIL" ? 1 : 0));
  const hl = pts.find((p) => p.n.branch_id === highlight);
  return (
    <div className="fan-wrap" onMouseLeave={() => setHover(null)}>
      <svg className="fan" viewBox={`0 0 ${W} ${H}`} role="group" aria-label={`${kids.length} worlds explored from one baseline; ${pts.filter((p) => p.st === "FAIL").length} broke a rule`}>
        {order.map((p) => {
          const mx = cx + (p.x - cx) * 0.42;
          const my = cy - 18;
          return <path key={`l-${p.n.branch_id}`} d={`M${cx},${cy} Q${mx},${my} ${p.x},${p.y}`} className={`fan-link fan-link-${p.st.toLowerCase()} ${hover?.n === p.n ? "is-hover" : ""}`} />;
        })}
        {order.map((p) => (
          <g
            key={p.n.branch_id}
            className={`fan-node fan-${p.st.toLowerCase()}`}
            transform={`translate(${p.x},${p.y})`}
            tabIndex={0}
            role="button"
            aria-label={`World ${p.n.branch_id.slice(-7)}: ${p.st === "FAIL" ? "broke " + p.n.failing_invariants.join(", ") : "held"}. ${p.n.schedule.map(interventionSentence).join("; ")}`}
            onMouseEnter={() => setHover({ n: p.n, x: p.x, y: p.y })}
            onFocus={() => setHover({ n: p.n, x: p.x, y: p.y })}
            onBlur={() => setHover(null)}
            onClick={() => onPick(p.n.branch_id)}
            onKeyDown={(e) => e.key === "Enter" && onPick(p.n.branch_id)}
          >
            <circle r="11" className="hit" />
            {p.st === "FAIL" ? <path d="M0,-6.5L6.5,0L0,6.5L-6.5,0z" className="m" /> : p.st === "UNKNOWN" ? <circle r="4" className="m" /> : <circle r="3.6" className="m" />}
          </g>
        ))}
        {hl && (
          <g className="fan-hl" transform={`translate(${hl.x},${hl.y})`} pointerEvents="none">
            <circle r="12" />
          </g>
        )}
        <g className="fan-root" transform={`translate(${cx},${cy})`}>
          <circle r="7" />
          <text y="26" textAnchor="middle">
            The recorded hour
          </text>
        </g>
        {hl && (
          <text className="fan-hl-label" x={hl.x + (hl.x < cx ? -18 : 18)} y={hl.y + 4} textAnchor={hl.x < cx ? "end" : "start"}>
            narrowed in step 3
          </text>
        )}
      </svg>
      {hover && (
        <div className="viz-tip" style={{ left: `${(hover.x / W) * 100}%`, top: `${(hover.y / H) * 100}%` }} role="status">
          <div className="tip-head">
            <VerdictGlyph v={nodeStatus(hover.n)} /> <b>{nodeStatus(hover.n) === "FAIL" ? "Broke a rule" : nodeStatus(hover.n) === "UNKNOWN" ? "Unknown" : "Held"}</b>
            <code className="tip-id">{hover.n.branch_id.replace(/^root\/(sha256:)?/, "").slice(0, 7)}</code>
          </div>
          <ul>
            {hover.n.schedule.map((iv, i) => (
              <li key={i}>{interventionSentence(iv)}</li>
            ))}
          </ul>
          {hover.n.failing_invariants.length > 0 && <div className="tip-foot">fails {hover.n.failing_invariants.map(humanName).join(", ")}</div>}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ 2. timeline

function BeatTimeline({ data, registry, onInspect }: Props) {
  const m = data.minimal;
  const { trace, loading } = useTrace(registry, m.minimal_branch_id);
  const d = m.causal_diff?.decisions?.[0];
  const correctionId = m.minimal[0]?.target.event_id ?? m.witness[0];
  const lede = (() => {
    if (!trace || !d) return <>The smallest failing replay, lane by lane: what actually happened, what each team knew, and when.</>;
    const actor = trace.scenario.actors.find((a) => a.id === d.actor);
    const rule = actor?.decision_rules.find((r) => r.id === d.rule_id);
    return (
      <>
        One {eventWord(correctionId)}, saying an earlier radar contact was a ghost, was held back {humanDuration(Number(m.minimal[0]?.params.by_ns ?? 0))}. The {humanName(d.actor)} had to decide at {rule ? mmss(rule.at_ns) : "the deadline"}, did not have it yet, and chose
        to <b>{d.option_b}</b>. With it, the right call was to <b>{d.option_a}</b>.
      </>
    );
  })();
  return (
    <Beat n={2} lede={lede} inspect={{ label: "Inspect this timeline", onClick: () => onInspect("timeline", m.minimal_branch_id) }}>
      {loading || !trace ? <TimelinePlaceholder missing={!loading} /> : <StoryTimeline data={data} trace={trace} correctionId={correctionId} />}
    </Beat>
  );
}

interface LaneSpec {
  id: string;
  title: string;
  sub: string;
  events: { e: WEvent; at: number }[];
}

function StoryTimeline({ data, trace, correctionId }: { data: LoadedData; trace: Trace; correctionId: string }) {
  const m = data.minimal;
  const d = m.causal_diff?.decisions?.[0];
  const decActor = trace.scenario.actors.find((a) => a.id === d?.actor) ?? trace.scenario.actors[0];
  const decRole = decActor.role;
  const rule = decActor.decision_rules.find((r) => r.id === d?.rule_id) ?? decActor.decision_rules[0];
  const decAt = rule.at_ns;
  const horizon = trace.scenario.clock.horizon_ns;
  const decision = trace.decisions.find((x) => x.payload.rule_id === rule.id);
  const otherRoles = Object.keys(trace.views).filter((r) => r !== decRole && r !== "evaluator");

  const lanes: LaneSpec[] = [
    { id: "truth", title: "What actually happened", sub: "ground truth, at the moment each thing occurred", events: trace.truth.events.map((e) => ({ e, at: e.event_time_ns })) },
    { id: decRole, title: cap(`${humanName(decRole)} · ${humanName(decActor.id)}`), sub: "makes the call; sees each message when it arrives", events: trace.views[decRole].events.map((e) => ({ e, at: e.received_time_ns ?? e.event_time_ns })) },
    ...otherRoles.slice(0, 1).map((r) => ({ id: r, title: cap(humanName(r)), sub: "slower link; sees a subset", events: trace.views[r].events.map((e) => ({ e, at: e.received_time_ns ?? e.event_time_ns })) })),
  ];

  const corrTruth = trace.truth.events.find((e) => e.id === correctionId);
  const corrView = trace.views[decRole]?.events.find((e) => e.id === correctionId);
  const moved = m.causal_diff?.views?.[decRole]?.received_time_moved.find((x) => x.event_id === correctionId);
  const arrivedAt = corrView?.received_time_ns ?? moved?.received_b_ns ?? decAt;
  const wouldHave = moved?.received_a_ns ?? corrTruth?.event_time_ns ?? decAt;
  const late = arrivedAt - decAt;

  // Start one minute after the decision: it is made, and the correction is still in transit.
  const [t, setT] = useState(Math.min(horizon, decAt + 60 * NS));
  const [playing, setPlaying] = useState(false);
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last = performance.now();
    const step = (now: number) => {
      const dt = now - last;
      last = now;
      setT((prev) => {
        const nx = prev + (dt / 8000) * horizon;
        if (nx >= horizon) {
          setPlaying(false);
          return horizon;
        }
        return nx;
      });
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [playing, horizon]);

  const [ref, w] = useWidth<HTMLDivElement>();
  const narrow = w < 640;
  const padL = 16;
  const padR = 16;
  // Narrow screens zoom into the minutes around the decision so the story stays legible.
  const v0 = narrow ? Math.max(0, Math.floor((Math.min(wouldHave, decAt) - 540 * NS) / (300 * NS)) * 300 * NS) : 0;
  const v1 = narrow ? Math.min(horizon, Math.ceil((Math.max(arrivedAt, decAt) + 420 * NS) / (300 * NS)) * 300 * NS) : horizon;
  const X = (ns: number) => padL + ((Math.max(v0, Math.min(v1, ns)) - v0) / (v1 - v0)) * (w - padL - padR);
  const inView = (ns: number) => ns >= v0 && ns <= v1;
  const bend = Math.min(40, Math.max(6, (X(arrivedAt) - X(wouldHave)) / 3));
  const laneGap = narrow ? 92 : 96;
  const top = 34;
  const laneY = (i: number) => top + 56 + i * laneGap;
  const H = laneY(lanes.length - 1) + 50;
  const tickStep = (narrow ? 300 : 600) * NS;
  const ticks = Array.from({ length: Math.floor((v1 - v0) / tickStep) + 1 }, (_, i) => v0 + i * tickStep);

  const known = trace.views[decRole].events.filter((e) => (e.received_time_ns ?? 0) <= t).length;
  const total = trace.truth.events.length;
  const corrKnown = arrivedAt <= t;
  const decided = t >= decAt;
  const reason = typeof corrTruth?.payload.reason === "string" ? (corrTruth.payload.reason as string) : null;
  const decLane = 1;

  return (
    <div className="viz-card">
      <div ref={ref} className="tl-story-wrap">
        <svg className="tl-story" width={w} height={H} role="img" aria-label={`Timeline: the ${eventWord(correctionId)} reached ${humanName(decRole)} ${humanDuration(late)} after the decision`}>
          <defs>
            <marker id="arrow-fail" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0L10,5L0,10z" fill="var(--fail)" />
            </marker>
          </defs>
          {ticks.map((tk) => (
            <g key={tk}>
              <line x1={X(tk)} x2={X(tk)} y1={top} y2={H - 18} className="tl-grid" />
              <text x={X(tk)} y={top - 10} className="tl-tick" textAnchor={tk === v0 ? "start" : tk >= v1 ? "end" : "middle"}>
                {narrow ? mmss(tk).replace(/:00$/, "") + (tk === v0 ? " min" : "") : tk === 0 ? "0 min" : `${Math.round(tk / NS / 60)} min`}
              </text>
            </g>
          ))}

          {/* scrubber shading: the not-yet-happened future */}
          <rect x={X(t)} y={top} width={Math.max(0, X(v1) - X(t))} height={H - 18 - top} className="tl-future" />

          {/* decision marker across all lanes */}
          <line x1={X(decAt)} x2={X(decAt)} y1={top} y2={H - 18} className="tl-decision-line" />

          {lanes.map((lane, i) => {
            const y = laneY(i);
            return (
              <g key={lane.id}>
                <text x={padL} y={y - 30} className="tl-lane-title">
                  {lane.title}
                </text>
                {!narrow && (
                  <text x={padL} y={y - 16} className="tl-lane-sub">
                    {lane.sub}
                  </text>
                )}
                <line x1={X(v0)} x2={X(v1)} y1={y} y2={y} className="tl-lane-line" />
                {lane.events.map(({ e, at }, k) => {
                  if (e.id === correctionId || !inView(at)) return null;
                  return <circle key={`${e.id}-${k}`} cx={X(at)} cy={y} r={2.6} className={`tl-ev ${at <= t ? "" : "tl-ev-future"}`} />;
                })}
              </g>
            );
          })}

          {/* truth: the correction as it happened */}
          {corrTruth && (
            <g className="tl-corr">
              <circle cx={X(corrTruth.event_time_ns)} cy={laneY(0)} r={5.5} className="tl-corr-dot" />
              <text x={X(corrTruth.event_time_ns) - 10} y={laneY(0) + 24} className="tl-ann" textAnchor="end">
                {narrow ? "Issued" : "Correction issued"} {mmss(corrTruth.event_time_ns)}
              </text>
              {reason && !narrow && (
                <text x={X(corrTruth.event_time_ns) - 10} y={laneY(0) + 40} className="tl-ann-sub" textAnchor="end">
                  “{reason.length > 46 ? reason.slice(0, 44) + "…" : reason}”
                </text>
              )}
            </g>
          )}

          {/* decider: where it would have arrived -> where it did arrive */}
          <g className="tl-corr">
            <circle cx={X(wouldHave)} cy={laneY(decLane)} r={5} className="tl-ghost" />
            <path d={`M${X(wouldHave) + 7},${laneY(decLane)} C${X(wouldHave) + bend},${laneY(decLane) - 26} ${X(arrivedAt) - bend},${laneY(decLane) - 26} ${X(arrivedAt) - 8},${laneY(decLane) - 3}`} className="tl-arrow" markerEnd="url(#arrow-fail)" />
            <circle cx={X(arrivedAt)} cy={laneY(decLane)} r={5.5} className={`tl-corr-dot ${corrKnown ? "" : "tl-corr-transit"}`} />
            {!narrow && (
              <text x={(X(wouldHave) + X(arrivedAt)) / 2 + 24} y={laneY(decLane) - 30} className="tl-ann tl-ann-fail" textAnchor="start">
                held back {humanDuration(arrivedAt - wouldHave)}
              </text>
            )}
            <text x={X(arrivedAt) + 10} y={laneY(decLane) + 26} className="tl-ann" textAnchor="start">
              arrives {mmss(arrivedAt)}
            </text>
          </g>

          {/* the decision itself */}
          <g className="tl-dec">
            <path d={`M${X(decAt)},${laneY(decLane) - 9}L${X(decAt) + 9},${laneY(decLane)}L${X(decAt)},${laneY(decLane) + 9}L${X(decAt) - 9},${laneY(decLane)}z`} className="tl-dec-mark" />
            <text x={X(decAt) - 12} y={laneY(decLane) + 26} className="tl-ann tl-ann-strong" textAnchor="end">
              {narrow ? `Decides ${mmss(decAt)}` : `Decides “${String(decision?.payload.option ?? d?.option_b ?? "")}” · ${mmss(decAt)}`}
            </text>
          </g>

          {/* scrubber line */}
          <g className="tl-scrub">
            <line x1={X(t)} x2={X(t)} y1={top - 4} y2={H - 18} />
            <circle cx={X(t)} cy={top - 4} r={4} />
          </g>
        </svg>
      </div>

      <div className="scrubber">
        <button type="button" className="btn btn-icon" onClick={() => (t >= horizon ? (setT(0), setPlaying(true)) : setPlaying(!playing))} aria-label={playing ? "Pause replay" : "Play replay"}>
          {playing ? (
            <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden>
              <rect x="3.5" y="3" width="3" height="10" rx="1" fill="currentColor" />
              <rect x="9.5" y="3" width="3" height="10" rx="1" fill="currentColor" />
            </svg>
          ) : (
            <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden>
              <path d="M4.5 3l8 5-8 5z" fill="currentColor" />
            </svg>
          )}
        </button>
        <input type="range" min={0} max={horizon} step={NS} value={t} onChange={(e) => (setPlaying(false), setT(Number(e.target.value)))} aria-label="Replay time" />
        <span className="scrub-time">{mmss(t)}</span>
      </div>
      <p className="scrub-caption" aria-live="polite">
        At <b>{mmss(t)}</b> the {humanName(decActor.id)} has received {known} of {total} messages.{" "}
        {corrKnown ? (
          <>The correction is in.</>
        ) : (
          <>
            The correction is <b className="fail-ink">still in transit</b>.
          </>
        )}{" "}
        {decided ? <>The decision is already made.</> : <>The decision is due at {mmss(decAt)}.</>}
      </p>
      <p className="callout">
        <VerdictGlyph v="FAIL" size={14} />
        <span>
          The correction arrived <b>{humanDuration(late)}</b> after the {humanName(decActor.id)} had already decided.
        </span>
      </p>
    </div>
  );
}

// ------------------------------------------------------------------ 3. one cause

function BeatOneCause({ data, onInspect }: { data: LoadedData; onInspect: Props["onInspect"] }) {
  const m = data.minimal;
  const keep = new Set(m.minimal.map(interventionText));
  return (
    <Beat
      n={3}
      lede={
        <>
          The failing replay changed {m.original.length} things at once. WORLDLINE takes them away and re-runs until only what is needed is left (a method called <em>delta debugging</em>). {m.minimal.length === 1 ? "One change" : `${m.minimal.length} changes`} remain: {interventionSentence(m.minimal[0]).toLowerCase()}. On {m.minimal.length === 1 ? "its own" : "their own"}, the decision still goes wrong.
        </>
      }
      inspect={{ label: "Inspect the reduction", onClick: () => onInspect("mechanism") }}
    >
      <div className="viz-card">
        <p className="viz-title">
          <span className="viz-title-num">{m.original.length}</span> changes tried, <span className="viz-title-num">{m.minimal.length}</span> {m.minimal.length === 1 ? "explains" : "explain"} it
        </p>
        <ol className="iv-cards">
          {m.original.map((iv, i) => {
            const kept = keep.has(interventionText(iv));
            return (
              <li key={i} className={`iv-card ${kept ? "iv-kept" : "iv-removed"}`}>
                <span className="iv-op">{iv.op}</span>
                <span className="iv-sentence">{interventionSentence(iv)}</span>
                {iv.target.event_id && <code className="iv-id">{iv.target.event_id}</code>}
                <span className="iv-status">
                  {kept ? (
                    <>
                      <VerdictGlyph v="FAIL" size={11} /> Alone, this still breaks the decision
                    </>
                  ) : (
                    <>Removed: the failure did not need it</>
                  )}
                </span>
              </li>
            );
          })}
        </ol>
        <div className="checks">
          <span className={`check ${m.one_minimal_verified ? "ok" : "nok"}`} title="Independent check: the minimal schedule fails and removing its intervention passes.">
            <CheckIcon ok={m.one_minimal_verified} /> {m.one_minimal_verified ? "Verified minimal" : "Not verified minimal"}
          </span>
          <span className={`check ${m.self_explaining ? "ok" : "nok"}`} title="The remaining intervention targets an event the failing verdict names as its witness.">
            <CheckIcon ok={m.self_explaining} /> {m.self_explaining ? "Targets the exact message the failure names" : "Does not target the message the failure names"}
          </span>
          <span className="check plain" title="Distinct schedules executed during the reduction">
            {m.oracle_calls} replays to get there
          </span>
        </div>
      </div>
    </Beat>
  );
}

function CheckIcon({ ok }: { ok: boolean }) {
  return ok ? (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden>
      <circle cx="8" cy="8" r="7" fill="var(--pass)" />
      <path d="M4.8 8.3l2.2 2.2 4.2-4.6" fill="none" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ) : (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden>
      <path d="M8 1L15 8 8 15 1 8z" fill="var(--fail)" />
      <path d="M6 6l4 4M10 6l-4 4" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

// ------------------------------------------------------------------ 4. genome

function BeatGenome({ data, onInspect }: { data: LoadedData; onInspect: Props["onInspect"] }) {
  const g = data.genome;
  const mech = g.mechanism[0];
  const sel = (mech?.selector ?? {}) as Record<string, string>;
  const kind = sel.kind ?? "message";
  const until = String(mech?.params?.until ?? "after_decision") === "after_decision" ? "just after the decision" : String(mech?.params?.until).replace(/_/g, " ");
  return (
    <Beat
      n={4}
      layout="split"
      lede={
        <>
          Strip out the names and timestamps and the cause becomes a reusable failure pattern, which WORLDLINE calls a <em>genome</em>. It applies to any system where its {g.preconditions.length} conditions hold, so you can test for it before it bites.
        </>
      }
      inspect={{ label: "Inspect the genome", onClick: () => onInspect("mechanism") }}
    >
      <div className="viz-card genome">
        <p className="genome-sentence">
          Hold back the last <b>{kind}</b> a decider relies on until {until}, and the decision no longer matches the truth.
        </p>
        <div className="flow" aria-label="Mechanism diagram">
          <div className="flow-node">
            <span className="flow-k">kind</span>
            <span className="flow-v">{kind}</span>
            <span className="flow-s">{humanName(sel.relation ?? "")}</span>
          </div>
          <FlowArrow label={`${mech?.op ?? "delay"}ed`} />
          <div className="flow-node">
            <span className="flow-k">role</span>
            <span className="flow-v">{humanName(sel.role ?? "decision role")}</span>
            <span className="flow-s">{humanName(sel.position ?? "")}</span>
          </div>
          <FlowArrow label="decides without it" />
          <div className="flow-node flow-node-fail">
            <span className="flow-k">decision</span>
            <span className="flow-v">
              <VerdictGlyph v="FAIL" size={11} /> wrong
            </span>
            <span className="flow-s">{humanName(g.violated_invariant.split(".").pop() ?? g.violated_invariant)}</span>
          </div>
        </div>
        <ul className="chips" aria-label="Preconditions">
          {g.preconditions.map((p) => (
            <li key={p} className="chip-check" title={p}>
              <CheckIcon ok /> {preconditionText(p)}
            </li>
          ))}
        </ul>
        <div className="genome-meta">
          <ShortId value={g.id} label="genome" />
          <span className="kind-inline" title={KIND_HELP[g.evidence_kind]}>
            <KindGlyph kind={g.evidence_kind} size={14} /> {KIND_LABEL[g.evidence_kind]}
          </span>
        </div>
      </div>
    </Beat>
  );
}

function FlowArrow({ label }: { label: string }) {
  return (
    <div className="flow-arrow" aria-hidden>
      <span>{label}</span>
      <svg width="44" height="12" viewBox="0 0 44 12">
        <path d="M1 6h38M35 2l5 4-5 4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </div>
  );
}

// ------------------------------------------------------------------ 5. challenge

function cap(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function scenarioTitle(id: string): string {
  const words = id.split("_");
  const immune = words[words.length - 1] === "immune";
  const base = (immune ? words.slice(0, -1) : words).join(" ").replace(/lostlink/, "lost link");
  return base.charAt(0).toUpperCase() + base.slice(1) + (immune ? " (immune twin)" : "");
}

function BeatChallenge({ data, registry, onInspect }: Props) {
  const c = data.challenge;
  return (
    <Beat
      n={5}
      lede={
        <>
          The same pattern, applied to a different scenario (a convoy that loses its data link), broke its decision the same way. A twin built to withstand it held, so the pattern points at a specific weakness, not at “any delay breaks everything”.
        </>
      }
      inspect={{ label: "Inspect the challenge", onClick: () => onInspect("mechanism") }}
    >
      <div className="pair">
        <ChallengeCard r={c.primary} kind="primary" onOpen={registry.has(challengeKey(c.primary.scenario_id)) ? () => onInspect("timeline", challengeKey(c.primary.scenario_id)) : undefined} />
        {c.controls.map((r) => (
          <ChallengeCard key={r.scenario_id} r={r} kind="control" />
        ))}
      </div>
      <p className="beat-next">
        What happens next lives in GitLab: an agent investigated this failure, another proposed a repair, and two reviewers judged it.{" "}
        <a className="ext" href={MERGE_REQUEST_URL} target="_blank" rel="noreferrer">
          Open the repair merge request
        </a>
      </p>
    </Beat>
  );
}

function ChallengeCard({ r, kind, onOpen }: { r: ChallengeResult; kind: "primary" | "control"; onOpen?: () => void }) {
  const checks = Object.values(r.preconditions_report);
  const met = checks.filter(Boolean).length;
  const fail = r.verdicts.find((v) => v.result === "FAIL");
  const p = fail ? parseDecisionExplanation(fail.explanation) : {};
  const expected = kind === "primary" ? r.reproduced : r.preconditions_met && !r.reproduced;
  const why =
    kind === "primary"
      ? p.chose && p.truth
        ? `The ${humanName(p.actor ?? "officer")} chose “${p.chose}” while the truth said “${p.truth}”.`
        : r.explanation
      : r.reproduced
        ? r.explanation
        : "Same conditions, same delay, yet every decision still matched the truth: the scenario was built to withstand it.";
  return (
    <article className={`ch-card ${r.reproduced ? "ch-broke" : "ch-held"} ${expected ? "" : "ch-unexpected"}`}>
      <span className="eyebrow">{kind === "primary" ? "Same pattern, new scenario" : "Control, built to withstand it"}</span>
      <h3>{scenarioTitle(r.scenario_id)}</h3>
      <div className="ch-outcome">
        <VerdictGlyph v={r.reproduced ? "FAIL" : "PASS"} size={18} />
        {r.reproduced ? "Reproduced" : "Not reproduced"}
      </div>
      <p className="ch-why">{why}</p>
      <div className="ch-meta">
        <span>
          {met}/{checks.length} conditions met
        </span>
        <span className={expected ? "ch-expected" : "fail-ink"}>{expected ? "✓ as predicted" : "✗ not as predicted"}</span>
      </div>
      {onOpen && (
        <button type="button" className="btn btn-ghost" onClick={onOpen}>
          See its timeline
        </button>
      )}
    </article>
  );
}

// ------------------------------------------------------------------ 6. evidence

function claimText(c: string): string {
  const parts = c.split(":");
  const tail = (s: string) => humanName(s.split(".").pop() ?? s);
  if (parts[0] === "invariant") return `Rule holds: ${tail(parts[1])}`;
  if (parts[0] === "counterexample") return `Counterexample: ${tail(parts[parts.length - 1])}`;
  const map: Record<string, string> = {
    "tests:unit": "Unit tests pass",
    "explore:guided_vs_random": "Guided vs random comparison",
    "minimize:one_minimal": "Single cause verified minimal",
    "genome:challenge": "Mechanism challenge result",
    "artifact:manifest": "Artifact manifest",
    "artifact:digest": "Artifact digest",
  };
  return map[c] ?? humanName(c.replace(/:/g, " · "));
}

function denialText(reason: string): string {
  const m = reason.match(/^integrity:record_edited@(\d+)/);
  if (m) return `Record ${m[1]} was edited after it was sealed: its content no longer matches its hash.`;
  const s = reason.match(/^stale/);
  if (s) return `Some evidence is older than the policy allows. (${reason})`;
  return reason;
}

function BeatEvidence({ data, onInspect }: { data: LoadedData; onInspect: Props["onInspect"] }) {
  const { bundle, authorization, authorizationDenied } = data;
  const [which, setWhich] = useState<"authorized" | "denied">("authorized");
  const [pick, setPick] = useState<number | null>(null);
  const auth: Authorization = which === "denied" && authorizationDenied ? authorizationDenied : authorization;
  const tampered = new Set<number>();
  if (auth.decision === "DENIED") for (const r of auth.reasons) for (const mm of r.matchAll(/@(\d+)/g)) tampered.add(Number(mm[1]));
  const satisfied = new Set(auth.satisfied.map((s) => s.record_id));
  const groups = useMemo(() => {
    const g = new Map<string, { r: EvidenceRecord; i: number }[]>();
    bundle.records.forEach((r, i) => {
      const k = r.producer === "kernel" ? "kernel" : "agent";
      g.set(k, [...(g.get(k) ?? []), { r, i }]);
    });
    return [...g.entries()].sort((a, b) => (a[0] === "agent" ? 0 : 1) - (b[0] === "agent" ? 0 : 1));
  }, [bundle]);
  const kindsPresent = [...new Set(bundle.records.map((r) => r.kind))] as EvidenceKind[];
  const picked = pick != null ? bundle.records[pick] : null;

  return (
    <Beat
      n={6}
      lede={
        <>
          Every result above is stored as an evidence record, labelled with how it is known: observed on a run, attested by the kernel, or proved within the model. Each record is linked by hash to the one before, and the chain is sealed with a signature. Edit one record and the release gate says no.
        </>
      }
      inspect={{ label: "Inspect every record", onClick: () => onInspect("evidence") }}
    >
      <div className="viz-card">
        <div className="viz-toolbar">
          <span className="viz-sub">
            {bundle.records.length} records, one chain, sealed {bundle.checkpoint?.sealed_at.replace("T", " ").replace("Z", " UTC") ?? ""}
          </span>
          <div className="legend" aria-label="Certainty types">
            {kindsPresent.map((k) => (
              <span key={k} title={KIND_HELP[k]}>
                <KindGlyph kind={k} size={14} /> {KIND_LABEL[k]}
              </span>
            ))}
          </div>
        </div>
        <div className="stepper">
          {groups.map(([producer, items]) => (
            <div key={producer} className="step-group">
              <div className="step-group-head">
                <b>{producer === "kernel" ? "Kernel" : "Agent"}</b>
                <span>{producer === "kernel" ? `${items.length} records it recomputed and signed off itself` : `${items.length} results the pipeline reports`}</span>
              </div>
              <ol className="steps-row">
                {items.map(({ r, i }) => (
                  <li key={i} className={`step ${tampered.has(i) ? "step-tampered" : ""} ${satisfied.has(r.id) && auth.decision === "AUTHORIZED" ? "step-used" : ""} ${pick === i ? "step-picked" : ""}`}>
                    <button type="button" onClick={() => setPick(pick === i ? null : i)} onMouseEnter={() => setPick(i)} onFocus={() => setPick(i)} aria-label={`Record ${i}: ${claimText(r.claim)}, ${KIND_LABEL[r.kind]}`} title={`#${i} ${claimText(r.claim)}\n${KIND_LABEL[r.kind]} · ${r.producer}`}>
                      <KindGlyph kind={r.kind} size={20} />
                    </button>
                    {tampered.has(i) && <span className="step-flag">edited</span>}
                  </li>
                ))}
              </ol>
            </div>
          ))}
        </div>
        <div className="record-peek" aria-live="polite">
          {picked ? (
            <>
              <span className="rp-idx">#{pick}</span>
              <KindGlyph kind={picked.kind} size={14} />
              <b>{claimText(picked.claim)}</b>
              <span className="muted">
                {KIND_LABEL[picked.kind]} · {picked.producer === "kernel" ? "kernel" : "agent"}
              </span>
              <ShortId value={picked.id} label="record" />
            </>
          ) : (
            <span className="muted">Point at a record to read its claim. Agents may report results; only the kernel (the trusted checker that holds the signing key) may attest them.</span>
          )}
        </div>

        <div className="gate">
          <div className="seg seg-lg" role="tablist" aria-label="Release gate outcome">
            <button type="button" role="tab" aria-selected={which === "authorized"} className={`seg-btn ${which === "authorized" ? "active" : ""}`} onClick={() => setWhich("authorized")}>
              <span className="seg-main">AUTHORIZED</span>
              <span className="seg-sub">original bundle</span>
            </button>
            <button type="button" role="tab" aria-selected={which === "denied"} className={`seg-btn ${which === "denied" ? "active" : ""}`} onClick={() => setWhich("denied")} disabled={!authorizationDenied}>
              <span className="seg-main">DENIED</span>
              <span className="seg-sub">one record edited</span>
            </button>
          </div>
          <div className={`verdict-big ${auth.decision === "AUTHORIZED" ? "vb-ok" : "vb-no"}`}>
            <div className="vb-word">
              {auth.decision === "AUTHORIZED" ? <CheckIcon ok /> : <CheckIcon ok={false} />}
              {auth.decision}
            </div>
            <p className="vb-reason">
              {auth.decision === "AUTHORIZED" ? (
                <>
                  Release policy “{auth.policy}”: {(auth.reasons[0] ?? "all requirements met").split("(s)").join("s")}. Every required claim is backed by a typed record, and the chain and signature check out.
                </>
              ) : (
                <>{denialText(auth.reasons[0] ?? "")} Same policy, same key; nothing else changed.</>
              )}
            </p>
          </div>
        </div>
      </div>
    </Beat>
  );
}

// ------------------------------------------------------------------ 7. verify

function TimelinePlaceholder({ missing }: { missing: boolean }) {
  if (missing)
    return (
      <div className="viz-card tl-placeholder">
        <div className="state-inline">
          <WarnIcon />
          <div>
            <b>The timeline file isn't in this build</b>
            <p>The minimal trace was not copied into the data folder. Run <code>npm run copy-data</code> and rebuild; every other step still works.</p>
          </div>
        </div>
      </div>
    );
  return (
    <div className="viz-card tl-placeholder" aria-busy="true" aria-label="Loading the timeline">
      {[0, 1, 2].map((i) => (
        <div key={i} className="sk-lane">
          <span className="sk sk-line" style={{ width: 160 }} />
          <span className="sk sk-rule" />
        </div>
      ))}
      <span className="sk sk-line" style={{ width: "100%", height: 10, marginTop: 28 }} />
      <span className="sk sk-card" style={{ height: 56, marginTop: 22 }} />
    </div>
  );
}

interface Finding {
  code: string;
  index?: number | null;
  detail?: string;
}
interface VerifyResult {
  ok: boolean;
  findings: Finding[];
  root: string | null;
  signer_key_id: string | null;
  verifier_key_id?: string;
  n_records?: number;
}
type Req<T> = { status: "idle" } | { status: "busy" } | { status: "done"; res: T; ms: number; source: string } | { status: "error"; kind: "network" | "rejected"; message: string };
type Source = { kind: "loaded" | "tampered"; name: string; records: number } | { kind: "file" | "paste"; name: string; text: string; records: number; bytes: number };

const FINDING_TEXT: Record<string, (i: number | null | undefined) => string> = {
  record_edited: (i) => `Record #${i} was edited after it was sealed: its content no longer matches its hash.`,
  record_dropped: (i) => (i != null ? `A record is missing from the chain near #${i}.` : "A record is missing from the chain."),
  records_reordered: (i) => `Records were reordered${i != null ? ` (first at #${i})` : ""}.`,
  unknown_signer: () => "The seal was signed with a key the verifier doesn't recognise.",
  signature_invalid: () => "The signature on the seal does not verify.",
  truncated: () => "The bundle has fewer records than its seal covers.",
  unsealed_tail: (i) => `Records were added after the seal${i != null ? `, from #${i}` : ""}.`,
  root_mismatch: () => "The chain no longer ends at the root the seal signed.",
  not_sealed: () => "The bundle was never sealed.",
};
function findingText(f: Finding): string {
  return FINDING_TEXT[f.code]?.(f.index) ?? f.detail ?? f.code;
}

function shortHex(h: string): string {
  return h.replace(/^sha256:/, "").slice(0, 7);
}

/** A kernel authorization reason in plain English. */
function reasonText(reason: string): string {
  let m = reason.match(/^integrity:([a-z_]+)(?:@(\d+))?/);
  if (m) return findingText({ code: m[1], index: m[2] != null ? Number(m[2]) : null, detail: reason });
  m = reason.match(/^stale: (\S+) \(.*?\) is (\d+)s old > (\d+)s/);
  if (m) return `“${claimText(m[1])}” is ${Math.round(Number(m[2]) / 3600)} h old; the policy accepts up to ${Math.round(Number(m[3]) / 3600)} h.`;
  m = reason.match(/^subject_mismatch: (\S+) \(.*?\) is about (\S+), not (\S+)/);
  if (m) return `“${claimText(m[1])}” is about artifact ${shortHex(m[2])}, not ${shortHex(m[3])}.`;
  m = reason.match(/^missing: no acceptable record for '(.*)'/);
  if (m) return `No acceptable record for “${claimText(m[1])}”.`;
  m = reason.match(/^all (\d+) requirement\(s\) met by (\d+) record\(s\)/);
  if (m) return `All ${m[1]} requirements met by ${m[2]} records.`;
  m = reason.match(/^downgraded: (\S+) \(.*?\) is (\S+) < (\S+)/);
  if (m) return `“${claimText(m[1])}” is only ${humanName(m[2].toLowerCase())}; the policy needs ${humanName(m[3].toLowerCase())}.`;
  return reason;
}

function parseBundle(text: string): { records: number } {
  let doc: unknown;
  try {
    doc = JSON.parse(text);
  } catch {
    throw new Error("This isn't valid JSON. Check that the whole file was pasted.");
  }
  const recs = (doc as { records?: unknown } | null)?.records;
  if (!doc || typeof doc !== "object" || !Array.isArray(recs)) throw new Error("This JSON isn't a WORLDLINE bundle: it needs a records[] array (format worldline.bundle.v1).");
  return { records: recs.length };
}

async function copyText(value: string) {
  try {
    await navigator.clipboard.writeText(value);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = value;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
}

function CopyButton({ value, label, text }: { value: string; label: string; text?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className={`copy-btn ${text ? "copy-btn-text" : ""}`}
      aria-label={label}
      title={label}
      onClick={async () => {
        await copyText(value);
        setCopied(true);
        setTimeout(() => setCopied(false), 1400);
      }}
    >
      {copied ? (
        <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden>
          <path d="M3 8.5l3 3 7-7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      ) : (
        <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden>
          <rect x="5" y="5" width="8.5" height="8.5" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
          <path d="M3 10.5V3.5A1 1 0 0 1 4 2.5h6.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
      )}
      {text && <span aria-hidden>{copied ? "Copied" : text}</span>}
      <span className="sr-only" aria-live="polite">
        {copied ? "Copied to clipboard" : ""}
      </span>
    </button>
  );
}

function WarnIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden className="warn-icon">
      <path d="M12 3.5l9.5 16.5h-19z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
      <path d="M12 10v4.5M12 17.2v.1" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function BigMark({ ok }: { ok: boolean }) {
  return ok ? (
    <svg width="30" height="30" viewBox="0 0 32 32" aria-hidden className="big-mark">
      <circle cx="16" cy="16" r="15" fill="var(--pass)" />
      <path d="M9.5 16.5l4.3 4.3 8.7-9.3" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ) : (
    <svg width="30" height="30" viewBox="0 0 32 32" aria-hidden className="big-mark">
      <path d="M16 1.5L30.5 16 16 30.5 1.5 16z" fill="var(--fail)" />
      <path d="M11.5 11.5l9 9M20.5 11.5l-9 9" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" />
    </svg>
  );
}

function Spinner() {
  return <span className="spinner" aria-hidden />;
}

function useHealth(): "checking" | "online" | "offline" {
  const [s, setS] = useState<"checking" | "online" | "offline">("checking");
  useEffect(() => {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 8000);
    fetch(HEALTH_ENDPOINT, { signal: ctl.signal, cache: "no-store" })
      // Read the body so the browser closes the request; an unread body keeps it in flight.
      .then(async (r) => {
        await r.text().catch(() => undefined);
        setS(r.ok ? "online" : "offline");
      })
      .catch(() => setS("offline"))
      .finally(() => clearTimeout(t));
    return () => {
      clearTimeout(t);
    };
  }, []);
  return s;
}

type ReqError = Error & { kind?: "network" | "rejected" };
function reqError(message: string, kind: "network" | "rejected"): ReqError {
  return Object.assign(new Error(message), { kind });
}

async function postJson<T>(url: string, body: string): Promise<{ res: T; ms: number }> {
  const t0 = performance.now();
  let res: Response;
  try {
    res = await fetch(url, { method: "POST", headers: { "content-type": "application/json" }, body });
  } catch (e) {
    throw reqError(e instanceof Error ? e.message : String(e), "network");
  }
  let json: unknown = null;
  try {
    json = await res.json();
  } catch {
    /* non-JSON body */
  }
  if (!res.ok) {
    const msg = (json as { error?: unknown } | null)?.error ?? `${res.status} ${res.statusText}`;
    throw reqError(typeof msg === "string" ? msg : JSON.stringify(msg), "rejected");
  }
  return { res: json as T, ms: performance.now() - t0 };
}

function fmtUtc(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toISOString().replace("T", " ").slice(0, 16) + " UTC";
}

function BeatVerify({ data }: { data: LoadedData }) {
  const nRecords = data.bundle.records.length;
  const releasedAt = data.authorization.evaluated_at ?? data.bundle.checkpoint?.sealed_at ?? "";
  const health = useHealth();
  const [source, setSource] = useState<Source>({ kind: "loaded", name: "bundle.json", records: nRecords });
  const [srcError, setSrcError] = useState<string | null>(null);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [paste, setPaste] = useState("");
  const [dragging, setDragging] = useState(false);
  const [verify, setVerify] = useState<Req<VerifyResult>>({ status: "idle" });
  const [gate, setGate] = useState<Req<Authorization>>({ status: "idle" });
  const [digest, setDigest] = useState(data.authorization.artifact_digest);
  const [asOfRelease, setAsOfRelease] = useState(true);
  const [digestError, setDigestError] = useState<string | null>(null);
  const [curlTab, setCurlTab] = useState<"verify" | "gate">("verify");

  const resetResults = () => {
    setVerify({ status: "idle" });
    setGate({ status: "idle" });
  };
  const pickBuiltIn = (kind: "loaded" | "tampered") => {
    setSource({ kind, name: kind === "loaded" ? "bundle.json" : "bundle_tampered.json", records: nRecords });
    setSrcError(null);
    setPasteOpen(false);
    resetResults();
  };
  const takeText = (text: string, name: string, kind: "file" | "paste") => {
    const bytes = new Blob([text]).size;
    if (bytes > MAX_BUNDLE_BYTES) {
      setSrcError(`${name} is ${(bytes / 1048576).toFixed(1)} MB; the verifier accepts up to ${MAX_BUNDLE_BYTES / 1048576} MB.`);
      return;
    }
    try {
      const { records } = parseBundle(text);
      setSource({ kind, name, text, records, bytes });
      setSrcError(null);
      resetResults();
    } catch (e) {
      setSrcError(e instanceof Error ? e.message : String(e));
    }
  };
  const readFile = async (f: File | undefined) => {
    if (!f) return;
    setPasteOpen(false);
    takeText(await f.text(), f.name, "file");
  };
  const bundleText = async (): Promise<string> => {
    if (source.kind === "file" || source.kind === "paste") return source.text;
    const r = await fetch(dataUrl(source.name), { cache: "no-cache" });
    if (!r.ok) throw reqError(`${source.name} is not in this build's data folder`, "rejected");
    return r.text();
  };
  const run = async <T,>(set: (r: Req<T>) => void, url: string) => {
    set({ status: "busy" });
    try {
      const body = await bundleText();
      const { res, ms } = await postJson<T>(url, body);
      set({ status: "done", res, ms, source: source.name });
    } catch (e) {
      set({ status: "error", kind: (e as ReqError).kind ?? "network", message: e instanceof Error ? e.message : String(e) });
    }
  };
  const gateUrl = `${AUTHORIZE_ENDPOINT}?artifact_digest=${encodeURIComponent(digest.trim())}&policy=${DEFAULT_POLICY}${asOfRelease && releasedAt ? `&now=${encodeURIComponent(releasedAt)}` : ""}`;
  const runVerify = () => run<VerifyResult>(setVerify, VERIFY_ENDPOINT);
  const runGate = () => {
    if (!digest.trim()) {
      setDigestError("Enter the artifact digest, for example the release's commit SHA.");
      return;
    }
    setDigestError(null);
    run<Authorization>(setGate, gateUrl);
  };

  const fileArg = source.kind === "paste" ? "bundle.json" : source.name;
  const curl =
    curlTab === "verify"
      ? `curl -X POST "${VERIFY_ENDPOINT}" \\\n  -H 'content-type: application/json' \\\n  --data-binary @${fileArg}`
      : `curl -X POST "${gateUrl}" \\\n  -H 'content-type: application/json' \\\n  --data-binary @${fileArg}`;
  const busy = verify.status === "busy" || gate.status === "busy";
  const host = VERIFIER_URL.replace(/^https:\/\//, "");

  return (
    <Beat
      n={7}
      lede={
        <>
          The verifier is a public service that runs apart from this page and holds only the kernel's public key, so it can check evidence but never create it. Give it a bundle (the signed file of evidence records that ships with a release) and it re-checks every hash link, the signature, and the release policy.
        </>
      }
      aside={
        <p className="beat-aside">
          Release{" "}
          <a href={RELEASE_URL} target="_blank" rel="noreferrer">
            {RELEASE_TAG}
          </a>{" "}
          · {nRecords} records sealed {fmtUtc(data.bundle.checkpoint?.sealed_at ?? releasedAt)}
        </p>
      }
    >
      <div className="tool">
        <div className="tool-head">
          <div className="tool-id">
            <span className="tool-logo" aria-hidden>
              <svg width="20" height="20" viewBox="0 0 20 20">
                <path d="M10 2l7 2.5v5c0 4-3 7-7 8.5C6 16.5 3 13.5 3 9.5v-5z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
                <path d="M7 10l2 2 4-4.5" stroke="currentColor" strokeWidth="1.7" fill="none" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </span>
            <div>
              <div className="tool-name">WORLDLINE Verifier</div>
              <div className="tool-desc">Public · read-only · public key only</div>
            </div>
          </div>
          <div className="tool-endpoint">
            <span className={`health health-${health}`} role="status">
              <span className="health-dot" aria-hidden />
              {health === "checking" ? "Checking" : health === "online" ? "Online" : "Unreachable"}
            </span>
            <code className="endpoint" title={VERIFIER_URL}>
              {host}
            </code>
            <CopyButton value={VERIFIER_URL} label="Copy verifier URL" />
            <a className="tool-docs" href={VERIFIER_DOCS_URL} target="_blank" rel="noreferrer">
              API docs
            </a>
          </div>
        </div>

        <div className="tool-row">
          <section className="tool-pane" aria-labelledby="vt-1">
            <h3 className="tool-step" id="vt-1">
              <span className="step-n">1</span> Choose a bundle
            </h3>
            <label
              className={`drop ${dragging ? "drop-over" : ""}`}
              onDragEnter={(e) => (e.preventDefault(), setDragging(true))}
              onDragOver={(e) => e.preventDefault()}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                readFile(e.dataTransfer.files?.[0]);
              }}
            >
              <input type="file" accept=".json,application/json" className="sr-only" aria-label="Choose a bundle JSON file" onChange={(e) => (readFile(e.target.files?.[0]), (e.target.value = ""))} />
              <span className="drop-icon" aria-hidden>
                <svg width="22" height="22" viewBox="0 0 24 24">
                  <path d="M12 15V4M7.5 8.5L12 4l4.5 4.5" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M4 14v4.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V14" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
                </svg>
              </span>
              <span className="drop-main">
                Drop a <code>bundle.json</code> here, or <u>browse</u>
              </span>
              <span className="drop-sub">JSON up to {MAX_BUNDLE_BYTES / 1048576} MB. Nothing is sent until you press Verify.</span>
            </label>
            <div className="shortcuts" role="group" aria-label="Bundle shortcuts">
              <button type="button" className={`chip-btn ${source.kind === "loaded" ? "on" : ""}`} aria-pressed={source.kind === "loaded"} onClick={() => pickBuiltIn("loaded")}>
                Use the loaded bundle
              </button>
              <button type="button" className={`chip-btn ${source.kind === "tampered" ? "on" : ""}`} aria-pressed={source.kind === "tampered"} onClick={() => pickBuiltIn("tampered")} title="The same bundle with one record edited after sealing">
                Use the edited copy
              </button>
              <button type="button" className={`chip-btn ${pasteOpen ? "on" : ""}`} aria-expanded={pasteOpen} aria-controls="vt-paste" onClick={() => setPasteOpen(!pasteOpen)}>
                Paste JSON
              </button>
            </div>
            {pasteOpen && (
              <div className="paste" id="vt-paste">
                <textarea
                  value={paste}
                  placeholder={'{ "format": "worldline.bundle.v1", "records": [ … ] }'}
                  aria-label="Bundle JSON"
                  spellCheck={false}
                  rows={5}
                  onChange={(e) => {
                    setPaste(e.target.value);
                    if (e.target.value.trim()) takeText(e.target.value, "pasted JSON", "paste");
                    else setSrcError(null);
                  }}
                />
              </div>
            )}
            {srcError ? (
              <p className="field-error" role="alert">
                <WarnIcon />
                <span>{srcError}</span>
              </p>
            ) : (
              <div className="source-chip" aria-live="polite">
                <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden>
                  <path d="M4 1.5h5.5L13 5v9a.5.5 0 0 1-.5.5h-8.5A.5.5 0 0 1 3.5 14V2a.5.5 0 0 1 .5-.5z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
                  <path d="M9.5 1.5V5H13" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
                </svg>
                <span className="sc-name">{source.name}</span>
                <span className="sc-meta">
                  {source.records} records
                  {source.kind === "loaded" && " · the bundle this page shows"}
                  {source.kind === "tampered" && " · one record edited after sealing"}
                  {(source.kind === "file" || source.kind === "paste") && ` · ${(source.bytes / 1024).toFixed(1)} KB`}
                </span>
                {(source.kind === "loaded" || source.kind === "tampered") && (
                  <a className="sc-dl" href={dataUrl(source.name)} download={source.name}>
                    Download
                  </a>
                )}
              </div>
            )}
            <div className="tool-cta">
              <button type="button" className="btn btn-primary btn-lg" onClick={runVerify} disabled={busy || !!srcError} aria-describedby="vt-verify-hint">
                {verify.status === "busy" ? (
                  <>
                    <Spinner /> Verifying…
                  </>
                ) : (
                  "Verify bundle"
                )}
              </button>
              <span className="tool-hint" id="vt-verify-hint">
                Re-checks every hash link and the seal's signature.
              </span>
            </div>
          </section>
          <section className="tool-result" aria-labelledby="vt-r1" aria-live="polite" aria-busy={verify.status === "busy"}>
            <h3 className="tool-result-h" id="vt-r1">
              Integrity
            </h3>
            <VerifyOutcome req={verify} onRetry={runVerify} onCurl={() => setCurlTab("verify")} />
          </section>
        </div>

        <div className="tool-row">
          <section className="tool-pane" aria-labelledby="vt-2">
            <h3 className="tool-step" id="vt-2">
              <span className="step-n">2</span> Check the release gate
            </h3>
            <p className="tool-hint">Would the “{DEFAULT_POLICY}” policy let this artifact ship on the chosen bundle?</p>
            <label className="field">
              <span className="field-label">Artifact digest</span>
              <input
                className={`input mono ${digestError ? "input-error" : ""}`}
                value={digest}
                onChange={(e) => (setDigest(e.target.value), setDigestError(null))}
                spellCheck={false}
                autoComplete="off"
                aria-invalid={!!digestError}
                aria-describedby="vt-digest-help"
              />
            </label>
            <span className="field-help" id="vt-digest-help">
              {digestError ? (
                <span className="field-error-inline">
                  <WarnIcon /> {digestError}
                </span>
              ) : digest.trim() === data.authorization.artifact_digest ? (
                "The commit this release is about, prefilled from the run."
              ) : (
                <>
                  Edited.{" "}
                  <button type="button" className="text-btn" onClick={() => setDigest(data.authorization.artifact_digest)}>
                    Reset to the release's digest
                  </button>
                </>
              )}
            </span>
            {releasedAt && (<label className="check-field">
              <input type="checkbox" checked={asOfRelease} onChange={(e) => setAsOfRelease(e.target.checked)} />
              <span>
                Evaluate as of the release, {fmtUtc(releasedAt)}
                <span className="field-help">Unchecked, it evaluates now, and evidence past the policy's age limit counts as stale.</span>
              </span>
            </label>)}
            <div className="tool-cta">
              <button type="button" className="btn btn-secondary btn-lg" onClick={runGate} disabled={busy || !!srcError}>
                {gate.status === "busy" ? (
                  <>
                    <Spinner /> Checking…
                  </>
                ) : (
                  "Check release"
                )}
              </button>
            </div>
          </section>
          <section className="tool-result" aria-labelledby="vt-r2" aria-live="polite" aria-busy={gate.status === "busy"}>
            <h3 className="tool-result-h" id="vt-r2">
              Gate decision
            </h3>
            <GateOutcome req={gate} onRetry={runGate} onCurl={() => setCurlTab("gate")} />
          </section>
        </div>

        <div className="tool-curl" id="vt-curl">
          <div className="curl-head">
            <span className="curl-title">Run it from a terminal</span>
            <div className="seg seg-sm" role="tablist" aria-label="Command">
              <button type="button" role="tab" aria-selected={curlTab === "verify"} className={`seg-btn ${curlTab === "verify" ? "active" : ""}`} onClick={() => setCurlTab("verify")}>
                Verify
              </button>
              <button type="button" role="tab" aria-selected={curlTab === "gate"} className={`seg-btn ${curlTab === "gate" ? "active" : ""}`} onClick={() => setCurlTab("gate")}>
                Release gate
              </button>
            </div>
            <CopyButton value={curl} label="Copy curl command" text="Copy" />
          </div>
          <pre className="code-block" tabIndex={0} aria-label="curl command">
            {curl}
          </pre>
        </div>
      </div>
    </Beat>
  );
}

function OutcomeEmpty({ title, body }: { title: string; body: string }) {
  return (
    <div className="outcome-empty">
      <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden>
        <path d="M14 3l9 3.2v6.4c0 5.2-3.9 9.1-9 11.1-5.1-2-9-5.9-9-11.1V6.2z" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" strokeDasharray="3 2.5" />
      </svg>
      <b>{title}</b>
      <span>{body}</span>
    </div>
  );
}

function OutcomeBusy() {
  return (
    <div className="outcome-busy">
      <span className="sk sk-banner" />
      <span className="sk sk-line" style={{ width: "70%" }} />
      <span className="sk sk-line" style={{ width: "45%" }} />
      <span className="outcome-busy-label">
        <Spinner /> Waiting for the verifier…
      </span>
    </div>
  );
}

function OutcomeError({ kind, message, onRetry, onCurl }: { kind: "network" | "rejected"; message: string; onRetry: () => void; onCurl: () => void }) {
  return (
    <div className="outcome-error" role="alert">
      <div className="oe-head">
        <WarnIcon />
        <b>{kind === "network" ? "Couldn't reach the verifier" : "The verifier turned this request away"}</b>
      </div>
      <p>{kind === "network" ? "The request got no answer. The service may be waking up: try again in a few seconds, or run the same check from a terminal." : "Nothing was checked. The service said:"}</p>
      <code className="oe-msg">{message}</code>
      <div className="oe-actions">
        <button type="button" className="btn btn-sm" onClick={onRetry}>
          Try again
        </button>
        <button
          type="button"
          className="btn btn-sm btn-ghost"
          onClick={() => {
            onCurl();
            document.getElementById("vt-curl")?.scrollIntoView({ block: "center", behavior: "smooth" });
          }}
        >
          Show the curl command
        </button>
      </div>
    </div>
  );
}

function VerifyOutcome({ req, onRetry, onCurl }: { req: Req<VerifyResult>; onRetry: () => void; onCurl: () => void }) {
  if (req.status === "idle") return <OutcomeEmpty title="No check run yet" body="Choose a bundle and press Verify bundle. The answer comes straight from the live verifier." />;
  if (req.status === "busy") return <OutcomeBusy />;
  if (req.status === "error") return <OutcomeError kind={req.kind} message={req.message} onRetry={onRetry} onCurl={onCurl} />;
  const r = req.res;
  const first = r.findings.find((f) => f.index != null) ?? r.findings[0];
  return (
    <div className="outcome">
      <div className={`banner ${r.ok ? "banner-ok" : "banner-no"}`}>
        <BigMark ok={r.ok} />
        <div>
          <div className="banner-title">{r.ok ? "Intact" : "Tampered"}</div>
          <div className="banner-sub">{r.ok ? `All ${r.n_records ?? ""} records match their hashes and the seal's signature is valid.` : first ? findingText(first) : "The bundle failed verification."}</div>
        </div>
      </div>
      {!r.ok && r.findings.length > 1 && (
        <ul className="findings" aria-label="Other findings">
          {r.findings.filter((f) => f !== first).map((f, i) => (
            <li key={i}>
              {f.index != null && <span className="f-idx">#{f.index}</span>}
              <span className="f-text">{findingText(f)}</span>
              <code className="f-code">{f.code}</code>
            </li>
          ))}
        </ul>
      )}
      <dl className="outcome-meta">
        {!r.ok && first && (
          <div>
            <dt>Finding</dt>
            <dd>
              <code className="f-code">{first.code}</code>
            </dd>
          </div>
        )}
        {r.n_records != null && (
          <div>
            <dt>Records</dt>
            <dd>{r.n_records}</dd>
          </div>
        )}
        {r.signer_key_id && (
          <div>
            <dt>Signed by</dt>
            <dd>
              <ShortId value={r.signer_key_id} n={7} />
            </dd>
          </div>
        )}
        {r.root && (
          <div>
            <dt>Sealed root</dt>
            <dd>
              <ShortId value={r.root} />
            </dd>
          </div>
        )}
        <div>
          <dt>Checked</dt>
          <dd>
            {req.source} · {Math.round(req.ms)} ms
          </dd>
        </div>
      </dl>
    </div>
  );
}

function GateOutcome({ req, onRetry, onCurl }: { req: Req<Authorization>; onRetry: () => void; onCurl: () => void }) {
  if (req.status === "idle") return <OutcomeEmpty title="No gate check yet" body="The kernel's release policy answers AUTHORIZED or DENIED from the bundle and the artifact digest." />;
  if (req.status === "busy") return <OutcomeBusy />;
  if (req.status === "error") return <OutcomeError kind={req.kind} message={req.message} onRetry={onRetry} onCurl={onCurl} />;
  const a = req.res;
  const ok = a.decision === "AUTHORIZED";
  const reasons = [...new Set(a.reasons.map(reasonText))];
  return (
    <div className="outcome">
      <div className={`banner ${ok ? "banner-ok" : "banner-no"}`}>
        <BigMark ok={ok} />
        <div>
          <div className="banner-title">{ok ? "Authorized" : "Denied"}</div>
          <div className="banner-sub">{ok ? `${reasons[0] ?? "All requirements met."} Policy “${a.policy}”.` : reasons[0] ?? "The policy refused this release."}</div>
        </div>
      </div>
      {!ok && reasons.length > 1 && (
        <ul className="findings" aria-label="Reasons">
          {reasons.slice(1, 4).map((r) => (
            <li key={r}>
              <span className="f-text">{r}</span>
            </li>
          ))}
          {reasons.length > 4 && <li className="muted">and {reasons.length - 4} more</li>}
        </ul>
      )}
      <dl className="outcome-meta">
        <div>
          <dt>Artifact</dt>
          <dd>
            <ShortId value={a.artifact_digest} />
          </dd>
        </div>
        <div>
          <dt>Backed by</dt>
          <dd>{a.satisfied.length} records</dd>
        </div>
        <div>
          <dt>Checked</dt>
          <dd>
            {req.source} · {Math.round(req.ms)} ms
          </dd>
        </div>
      </dl>
    </div>
  );
}
