import { useEffect, useMemo, useRef, useState } from "react";
import type { Authorization, BranchNode, ChallengeResult, EvidenceKind, EvidenceRecord, ExplorationRun, LoadedData, Trace, WEvent } from "../types";
import { dataUrl, useTrace, challengeKey, type TraceEntry } from "../data";
import { eventWord, humanDuration, humanName, interventionSentence, interventionText, mmss, nodeStatus, parseDecisionExplanation, preconditionText, NS } from "../format";
import { KIND_HELP, KIND_LABEL, KindGlyph, VerdictGlyph } from "../components/Badges";
import { ShortId } from "../components/ShortId";
import { RELEASE_TAG, RELEASE_URL, VERIFIER_URL, VERIFY_ENDPOINT } from "../config";

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
      <Hero data={data} totalWorlds={totalWorlds} totalFailing={totalFailing} />
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

const BEATS = ["One history, many worlds", "The world that broke", "One cause", "A mechanism, not a bug", "It happens elsewhere", "Nothing ships on untyped evidence", "Check it yourself"];
const BEAT_SHORT = ["Many worlds", "The break", "One cause", "The mechanism", "Elsewhere", "The evidence", "Verify it"];

function Beat({ n, title, lede, layout = "stack", aside, inspect, children }: { n: number; title: string; lede: React.ReactNode; layout?: "stack" | "split"; aside?: React.ReactNode; inspect?: { label: string; onClick: () => void }; children: React.ReactNode }) {
  return (
    <section className={`beat beat-${layout}`} id={`beat-${n}`} aria-labelledby={`beat-${n}-h`}>
      <div className="beat-in">
        <header className="beat-head">
          <span className="beat-num">{String(n).padStart(2, "0")}</span>
          <h2 id={`beat-${n}-h`}>{title}</h2>
          <p className="beat-lede">{lede}</p>
          {aside}
          {inspect && (
            <button className="inspect-link" onClick={inspect.onClick}>
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

function Hero({ data, totalWorlds, totalFailing }: { data: LoadedData; totalWorlds: number; totalFailing: number }) {
  const auth = data.authorization;
  return (
    <section className="hero" aria-labelledby="hero-h">
      <div className="hero-in">
        <p className="eyebrow">Counterfactual testing for decisions made on late information</p>
        <h1 id="hero-h">When a message arrives late, does the decision still hold?</h1>
        <p className="hero-lede">
          WORLDLINE replays one synthetic hour of a harbour alert in {totalWorlds} slightly different worlds, finds the ones where a decision goes wrong, shrinks each to a single cause, and ships the proof as signed, typed evidence.
        </p>
        <dl className="hero-facts">
          <div>
            <dt>Worlds replayed</dt>
            <dd>{totalWorlds}</dd>
          </div>
          <div>
            <dt>Broke a rule</dt>
            <dd>{totalFailing}</dd>
          </div>
          <div>
            <dt>Root cause</dt>
            <dd>{data.minimal.minimal.length} intervention</dd>
          </div>
          <div>
            <dt>Release gate</dt>
            <dd className="hero-gate">{auth.decision === "AUTHORIZED" ? "Authorized" : "Denied"}</dd>
          </div>
        </dl>
        <ol className="beat-index" aria-label="The story in seven steps">
          {BEATS.map((b, i) => (
            <li key={b}>
              <a href={`#beat-${i + 1}`} title={b} onClick={(e) => (e.preventDefault(), document.getElementById(`beat-${i + 1}`)?.scrollIntoView({ behavior: "smooth" }))}>
                <span>{i + 1}</span>
                {BEAT_SHORT[i]}
              </a>
            </li>
          ))}
        </ol>
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
      title="One history, many worlds"
      layout="split"
      lede={
        <>
          We replayed the same hour {totalWorlds} times, each time nudging when one or more messages arrive. In {totalFailing} of those worlds a safety rule broke; every one is marked in red.
        </>
      }
      aside={g && r ? <GuidedVsRandom g={g} r={r} /> : null}
      inspect={{ label: "Inspect all worlds", onClick: () => onInspect("worlds", data.minimal.branch_id) }}
    >
      <div className="viz-card">
        <div className="viz-toolbar">
          <div className="seg" role="tablist" aria-label="Search strategy">
            {strategies.map((s) => (
              <button key={s} role="tab" aria-selected={s === strategy} className={`seg-btn ${s === strategy ? "active" : ""}`} onClick={() => setStrategy(s)}>
                {s === "guided" ? "Guided search" : s === "random" ? "Random search" : s}
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
            failing worlds, <b>guided</b>
          </div>
        </div>
        <div>
          <div className="big-num big-num-muted">{r.n_failing}</div>
          <div className="big-label">failing worlds, random</div>
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
        {g.first_decision_fail_index != null ? (
          <>
            Guided search hit a <b>wrong decision</b> in world #{g.first_decision_fail_index}; random search {r.first_decision_fail_index == null ? "never did within the same budget" : `took until world #${r.first_decision_fail_index}`}.
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
      <svg className="fan" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${kids.length} worlds explored from one baseline; ${pts.filter((p) => p.st === "FAIL").length} broke a rule`}>
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
            The real hour (baseline)
          </text>
        </g>
        {hl && (
          <text className="fan-hl-label" x={hl.x + (hl.x < cx ? -18 : 18)} y={hl.y + 4} textAnchor={hl.x < cx ? "end" : "start"}>
            shrunk in step 3
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
    if (!trace || !d) return <>The smallest failing world, replayed lane by lane: what actually happened, and what each team knew when.</>;
    const actor = trace.scenario.actors.find((a) => a.id === d.actor);
    const rule = actor?.decision_rules.find((r) => r.id === d.rule_id);
    return (
      <>
        A single {eventWord(correctionId)}, saying an earlier radar contact was a ghost, was held back {humanDuration(Number(m.minimal[0]?.params.by_ns ?? 0))}. The {humanName(d.actor)} decided at {rule ? mmss(rule.at_ns) : "the deadline"} without it and chose
        to <b>{d.option_b}</b>, where the truth said <b>{d.option_a}</b>.
      </>
    );
  })();
  return (
    <Beat n={2} title="The world that broke" lede={lede} inspect={{ label: "Inspect this timeline", onClick: () => onInspect("timeline", m.minimal_branch_id) }}>
      {loading || !trace ? <div className="viz-card viz-loading">{loading ? "Loading the timeline…" : "The minimal trace file is missing from the data folder."}</div> : <StoryTimeline data={data} trace={trace} correctionId={correctionId} />}
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
        <button className="btn btn-icon" onClick={() => (t >= horizon ? (setT(0), setPlaying(true)) : setPlaying(!playing))} aria-label={playing ? "Pause replay" : "Play replay"}>
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
          The correction arrived <b>{humanDuration(late)}</b> after the officer decided.
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
      title="One cause"
      lede={
        <>
          The failing world carried {m.original.length} interventions at once. Taking them away systematically shows only one is needed: {interventionSentence(m.minimal[0]).toLowerCase()}, and the decision still goes wrong.
        </>
      }
      inspect={{ label: "Inspect the reduction", onClick: () => onInspect("mechanism") }}
    >
      <div className="viz-card">
        <p className="viz-title">
          <span className="viz-title-num">{m.original.length}</span> interventions tried, <span className="viz-title-num">{m.minimal.length}</span> explains it
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
            <CheckIcon ok={m.one_minimal_verified} /> Verified minimal
          </span>
          <span className={`check ${m.self_explaining ? "ok" : "nok"}`} title="The remaining intervention targets an event the failing verdict names as its witness.">
            <CheckIcon ok={m.self_explaining} /> Points at the evidence the verdict names
          </span>
          <span className="check plain" title="Distinct schedules executed during the reduction">
            {m.oracle_calls} replays needed
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
      title="A mechanism, not a bug"
      layout="split"
      lede={
        <>
          Strip out the names and timestamps, and the cause becomes a pattern any system can have. It needs only {g.preconditions.length} conditions to be true, and it is checkable wherever they are.
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
      title="It happens elsewhere"
      lede={
        <>
          We planted the same pattern in a different scenario, a convoy that loses its data link, and the decision broke the same way. In a twin scenario built to be immune, it did not, so the pattern is specific rather than “any delay breaks everything”.
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
      <span className="eyebrow">{kind === "primary" ? "Same pattern, new scenario" : "Control: built to be immune"}</span>
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
        <span className={expected ? "ch-expected" : "fail-ink"}>{expected ? "as predicted" : "not as predicted"}</span>
      </div>
      {onOpen && (
        <button className="btn btn-ghost" onClick={onOpen}>
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
      title="Nothing ships on untyped evidence"
      lede={
        <>
          Every claim above is a record with a certainty type, chained by hash to the one before and sealed with a signature. Change one byte of one record and the same release policy says no.
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
                <span>{producer === "kernel" ? `${items.length} records it recomputed and attested itself` : `${items.length} results the pipeline reports`}</span>
              </div>
              <ol className="steps-row">
                {items.map(({ r, i }) => (
                  <li key={i} className={`step ${tampered.has(i) ? "step-tampered" : ""} ${satisfied.has(r.id) && auth.decision === "AUTHORIZED" ? "step-used" : ""} ${pick === i ? "step-picked" : ""}`}>
                    <button onClick={() => setPick(pick === i ? null : i)} onMouseEnter={() => setPick(i)} onFocus={() => setPick(i)} aria-label={`Record ${i}: ${claimText(r.claim)}, ${KIND_LABEL[r.kind]}`} title={`#${i} ${claimText(r.claim)}\n${KIND_LABEL[r.kind]} · ${r.producer}`}>
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
            <span className="muted">Hover a record to read its claim. Agents may report results; only the kernel may attest them.</span>
          )}
        </div>

        <div className="gate">
          <div className="seg seg-lg" role="tablist" aria-label="Release gate outcome">
            <button role="tab" aria-selected={which === "authorized"} className={`seg-btn ${which === "authorized" ? "active" : ""}`} onClick={() => setWhich("authorized")}>
              <span className="seg-main">AUTHORIZED</span>
              <span className="seg-sub">original bundle</span>
            </button>
            <button role="tab" aria-selected={which === "denied"} className={`seg-btn ${which === "denied" ? "active" : ""}`} onClick={() => setWhich("denied")} disabled={!authorizationDenied}>
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

interface VerifyResult {
  ok: boolean;
  findings: { code: string; index?: number; detail?: string }[];
  root: string | null;
  signer_key_id: string | null;
  verifier_key_id?: string;
  n_records?: number;
}

function BeatVerify({ data }: { data: LoadedData }) {
  const [state, setState] = useState<{ status: "idle" } | { status: "busy"; which: string } | { status: "done"; which: string; res: VerifyResult; ms: number } | { status: "error"; which: string; error: string }>({ status: "idle" });
  const run = async (file: "bundle.json" | "bundle_tampered.json") => {
    setState({ status: "busy", which: file });
    const t0 = performance.now();
    try {
      const body = await (await fetch(dataUrl(file), { cache: "no-cache" })).text();
      const res = await fetch(VERIFY_ENDPOINT, { method: "POST", headers: { "content-type": "application/json" }, body });
      const json = (await res.json()) as VerifyResult & { error?: string };
      if (!res.ok) throw new Error(json.error ?? `${res.status} ${res.statusText}`);
      setState({ status: "done", which: file, res: json, ms: performance.now() - t0 });
    } catch (e) {
      setState({ status: "error", which: file, error: e instanceof Error ? e.message : String(e) });
    }
  };
  const curl = `curl -X POST -H 'content-type: application/json' \\\n  --data-binary @bundle.json \\\n  ${VERIFY_ENDPOINT}`;
  return (
    <Beat
      n={7}
      title="Check it yourself"
      layout="split"
      lede={
        <>
          The verifier is public and runs separately from this page. Send it this exact bundle and it re-checks every hash link and the signature against the kernel's public key.
        </>
      }
      aside={
        <div className="links">
          <a href={VERIFIER_URL} target="_blank" rel="noreferrer" className="ext">
            <span className="ext-k">Live verifier</span>
            <span className="ext-v">{VERIFIER_URL.replace(/^https:\/\//, "")}</span>
          </a>
          <a href={RELEASE_URL} target="_blank" rel="noreferrer" className="ext">
            <span className="ext-k">Release</span>
            <span className="ext-v">gitlab.com/shafir2/WorldLine · {RELEASE_TAG}</span>
          </a>
        </div>
      }
    >
      <div className="viz-card verify-card">
        <div className="verify-actions">
          <button className="btn btn-primary" onClick={() => run("bundle.json")} disabled={state.status === "busy"}>
            {state.status === "busy" && state.which === "bundle.json" ? "Verifying…" : "Verify this bundle"}
          </button>
          <button className="btn" onClick={() => run("bundle_tampered.json")} disabled={state.status === "busy"} title="Send the copy with one edited record">
            {state.status === "busy" && state.which === "bundle_tampered.json" ? "Verifying…" : "Try the edited copy"}
          </button>
        </div>
        <div className="verify-out" aria-live="polite">
          {state.status === "idle" && (
            <p className="muted">
              Sends <code>bundle.json</code> ({data.bundle.records.length} records) to <code>/api/verify</code> and shows what the server answers.
            </p>
          )}
          {state.status === "busy" && <p className="muted">Waiting for the verifier…</p>}
          {state.status === "error" && (
            <p className="fail-ink">
              Could not reach the verifier: {state.error}. You can run the same check from a terminal (below).
            </p>
          )}
          {state.status === "done" && (
            <div className={`vr ${state.res.ok ? "vr-ok" : "vr-no"}`}>
              <div className="vr-head">
                <CheckIcon ok={state.res.ok} />
                <b>{state.res.ok ? "Verified: chain intact, signature valid" : "Rejected"}</b>
                <span className="muted">
                  {state.which} · {Math.round(state.ms)} ms
                </span>
              </div>
              {state.res.findings.length > 0 && (
                <ul className="vr-findings">
                  {state.res.findings.map((f, i) => (
                    <li key={i}>
                      <code>{f.code}</code> {f.detail}
                    </li>
                  ))}
                </ul>
              )}
              <div className="vr-meta">
                {state.res.n_records != null && <span>{state.res.n_records} records</span>}
                {state.res.signer_key_id && <ShortId value={state.res.signer_key_id} label="signed by" />}
                {state.res.root && <ShortId value={state.res.root} label="root" />}
              </div>
            </div>
          )}
        </div>
        <details className="details">
          <summary>Or from a terminal</summary>
          <pre className="code-block">{curl}</pre>
        </details>
      </div>
    </Beat>
  );
}
