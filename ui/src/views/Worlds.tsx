import { useEffect, useMemo, useState } from "react";
import type { BranchNode, ComparisonRow, ExplorationRun, LoadedData } from "../types";
import type { TraceEntry } from "../data";
import { cmp, interventionText, nodeStatus, secs, shortBranch, shortId } from "../format";
import { ScheduleList } from "../components/Schedule";
import { VerdictPill } from "../components/Badges";
import { ShortId } from "../components/ShortId";

interface Props {
  data: LoadedData;
  registry: Map<string, TraceEntry>;
  selected: string;
  onSelect: (id: string) => void;
  onOpenTimeline: (id?: string) => void;
}

interface Laid {
  node: BranchNode;
  depth: number;
  row: number;
  x: number;
  y: number;
}

const COL_W = 250;
const ROW_H = 26;
const PAD_X = 40;
const PAD_Y = 24;
const MAX_ROWS = 24; // wide fan-outs (one parent, dozens of children) wrap into columns

const STATUS_RANK = { FAIL: 0, UNKNOWN: 1, PASS: 2 } as const;

function layout(nodes: BranchNode[]): { laid: Laid[]; width: number; height: number } {
  const byId = new Map(nodes.map((n) => [n.branch_id, n]));
  const children = new Map<string, BranchNode[]>();
  let root: BranchNode | undefined;
  for (const n of nodes) {
    if (!n.parent || !byId.has(n.parent)) {
      if (n.branch_id === "root" || !root) root = n;
      if (n.branch_id !== "root") children.set("__orphan__", [...(children.get("__orphan__") ?? []), n]);
      continue;
    }
    children.set(n.parent, [...(children.get(n.parent) ?? []), n]);
  }
  // FAIL first, then UNKNOWN, then PASS; stable by schedule text inside each group.
  for (const arr of children.values())
    arr.sort((a, b) => STATUS_RANK[nodeStatus(a)] - STATUS_RANK[nodeStatus(b)] || cmp(a.schedule.map(interventionText).join("|"), b.schedule.map(interventionText).join("|")));

  const laid: Laid[] = [];
  let row = 0;
  let maxDepth = 0;
  const visit = (n: BranchNode, depth: number): number => {
    maxDepth = Math.max(maxDepth, depth);
    const kids = children.get(n.branch_id) ?? [];
    if (!kids.length) {
      const r = row++;
      laid.push({ node: n, depth, row: r, x: 0, y: 0 });
      return r;
    }
    if (kids.length > MAX_ROWS && kids.every((k) => !(children.get(k.branch_id) ?? []).length)) {
      // leaf fan-out: wrap into columns of MAX_ROWS
      const perCol = Math.ceil(kids.length / Math.ceil(kids.length / MAX_ROWS));
      const start = row;
      kids.forEach((k, i) => {
        const col = Math.floor(i / perCol);
        maxDepth = Math.max(maxDepth, depth + 1 + col);
        laid.push({ node: k, depth: depth + 1 + col, row: start + (i % perCol), x: 0, y: 0 });
      });
      row = start + Math.min(perCol, kids.length);
      const r = (start + row - 1) / 2;
      laid.push({ node: n, depth, row: r, x: 0, y: 0 });
      return r;
    }
    const rows = kids.map((k) => visit(k, depth + 1));
    const r = (rows[0] + rows[rows.length - 1]) / 2;
    laid.push({ node: n, depth, row: r, x: 0, y: 0 });
    return r;
  };
  if (root) visit(root, 0);
  for (const o of children.get("__orphan__") ?? []) visit(o, 1);
  for (const l of laid) {
    l.x = PAD_X + l.depth * COL_W;
    l.y = PAD_Y + l.row * ROW_H;
  }
  return { laid, width: PAD_X * 2 + maxDepth * COL_W + 140, height: PAD_Y * 2 + Math.max(1, row) * ROW_H };
}

export default function Worlds({ data, registry, selected, onSelect, onOpenTimeline }: Props) {
  const ex = data.exploration;
  const strategies = ex.strategies.filter((s) => ex.runs[s]);
  const owner = strategies.find((s) => ex.runs[s].nodes.some((n) => n.branch_id === selected && n.branch_id !== "root"));
  const [strategy, setStrategy] = useState<string>(owner ?? strategies[0]);
  useEffect(() => {
    if (owner && owner !== strategy) setStrategy(owner);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);
  const run: ExplorationRun = ex.runs[strategy] ?? ex.runs[strategies[0]];

  const { laid, width, height } = useMemo(() => layout(run.nodes), [run]);
  const pos = useMemo(() => new Map(laid.map((l) => [l.node.branch_id, l])), [laid]);
  const [hover, setHover] = useState<{ id: string; x: number; y: number } | null>(null);
  const sel = run.nodes.find((n) => n.branch_id === selected) ?? Object.values(ex.runs).flatMap((r) => r.nodes).find((n) => n.branch_id === selected) ?? run.nodes[0];
  const counts = useMemo(() => {
    const c = { PASS: 0, FAIL: 0, UNKNOWN: 0 };
    for (const n of run.nodes) c[nodeStatus(n)]++;
    return c;
  }, [run]);
  const hovered = hover ? run.nodes.find((n) => n.branch_id === hover.id) : undefined;
  const selHasTrace = registry.has(sel.branch_id);

  return (
    <div className="view">
      <header className="view-head">
        <div>
          <h1>Worlds</h1>
          <p className="lede">
            Branch trees for <code>{ex.scenario_id}</code> (seed {ex.seed}, budget {ex.budget}, {ex.schedule_size} interventions per schedule). The same baseline world was explored twice: <b>guided</b> targets events a decision rule
            depends on; <b>random</b> samples interventions uniformly. Hover a node for its schedule, click to select, double-click to open its timeline.
          </p>
        </div>
        <div className="stat-row">
          <Stat label="branches" value={run.n_nodes} />
          <Stat label="FAIL" value={counts.FAIL} tone="fail" />
          <Stat label="PASS" value={counts.PASS} tone="pass" />
          <Stat label="UNKNOWN" value={counts.UNKNOWN} tone="unknown" />
          <Stat label="rejected" value={run.n_rejected} title="Schedules rejected before running: they violated the time rule (e.g. delivery before emission)" />
        </div>
      </header>

      <ComparisonTable comparison={ex.comparison} strategies={strategies} active={strategy} onPick={setStrategy} />

      <div className="split">
        <div className="panel tree-panel" onMouseLeave={() => setHover(null)}>
          <div className="toolbar">
            <div className="seg" role="tablist" aria-label="strategy">
              {strategies.map((s) => (
                <button key={s} role="tab" aria-selected={s === strategy} className={`seg-btn ${s === strategy ? "active" : ""}`} onClick={() => setStrategy(s)}>
                  {s} <span className="muted">· {ex.runs[s].n_nodes} nodes · {ex.runs[s].n_failing} failing</span>
                </button>
              ))}
            </div>
            <div className="legend legend-right">
              <span><i className="dot dot-fail" /> FAIL (any invariant)</span>
              <span><i className="dot dot-pass" /> PASS</span>
              <span><i className="dot dot-unknown" /> UNKNOWN (missing signal)</span>
              <span><i className="dot dot-trace" /> trace exported</span>
            </div>
          </div>
          <div className="tree-scroll">
            <svg className="tree" width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`branch tree (${strategy})`}>
              {laid.map((l) =>
                l.node.parent && pos.get(l.node.parent) ? (
                  <path
                    key={`link-${l.node.branch_id}`}
                    className="link"
                    d={(() => {
                      const p = pos.get(l.node.parent!)!;
                      const mx = p.x + (COL_W * 0.55);
                      return `M${p.x + 8},${p.y} C${mx},${p.y} ${mx},${l.y} ${l.x - 8},${l.y}`;
                    })()}
                  />
                ) : null,
              )}
              {laid.map((l) => {
                const st = nodeStatus(l.node);
                const isSel = l.node.branch_id === selected;
                const hasTrace = registry.has(l.node.branch_id);
                const fails = l.node.failing_invariants ?? [];
                return (
                  <g
                    key={l.node.branch_id}
                    className={`node node-${st.toLowerCase()} ${isSel ? "node-selected" : ""}`}
                    transform={`translate(${l.x},${l.y})`}
                    onMouseEnter={(e) => setHover({ id: l.node.branch_id, x: e.clientX, y: e.clientY })}
                    onMouseMove={(e) => setHover({ id: l.node.branch_id, x: e.clientX, y: e.clientY })}
                    onClick={() => onSelect(l.node.branch_id)}
                    onDoubleClick={() => onOpenTimeline(l.node.branch_id)}
                    tabIndex={0}
                    onKeyDown={(e) => e.key === "Enter" && onSelect(l.node.branch_id)}
                    role="button"
                    aria-label={`${l.node.branch_id} ${st}`}
                  >
                    {isSel && <circle r="11" className="sel-ring" />}
                    <circle r="6.5" />
                    {hasTrace && <circle r="2.2" cy="-10" className="trace-dot" />}
                    <text x="11" y="4" className="node-label">
                      {shortBranch(l.node.branch_id)}
                      {l.node.schedule.length ? (
                        <tspan className="node-sub"> {fails.length ? fails.map((f) => f.split(".").pop()).join(",") : l.node.schedule.map((iv) => iv.op).join("+")}</tspan>
                      ) : (
                        <tspan className="node-sub"> baseline</tspan>
                      )}
                    </text>
                  </g>
                );
              })}
            </svg>
          </div>
          {hover && hovered && (
            <div className="tooltip" style={{ left: hover.x + 14, top: hover.y + 14 }}>
              <div className="tt-title">
                <code>{shortBranch(hovered.branch_id)}</code> <VerdictPill v={nodeStatus(hovered)} />
              </div>
              <ScheduleList schedule={hovered.schedule} />
              {hovered.failing_invariants?.length > 0 && <div className="small bad">fails: {hovered.failing_invariants.join(", ")}</div>}
              <div className="muted small">
                trace {shortId(hovered.trace_digest)} · cost {(hovered.cost_ns / 1e6).toFixed(0)} ms
              </div>
            </div>
          )}
        </div>

        <aside className="panel side">
          <h2>Selected branch</h2>
          <p>{sel.branch_id === "root" ? <b>Baseline (the real hour)</b> : <ShortId value={sel.branch_id} label="branch" />}</p>
          <dl className="kv">
            <dt>parent</dt>
            <dd>{sel.parent ? (sel.parent === "root" ? "baseline" : <ShortId value={sel.parent} />) : "—"}</dd>
            <dt>trace digest</dt>
            <dd>
              <ShortId value={sel.trace_digest} />
            </dd>
            <dt>cost</dt>
            <dd>{secs(sel.cost_ns)} virtual</dd>
            <dt>schedule</dt>
            <dd>
              <ScheduleList schedule={sel.schedule} />
            </dd>
          </dl>
          <h3>Verdicts</h3>
          <ul className="verdicts">
            {sel.verdicts.map((v) => (
              <li key={v.invariant_id} className={`verdict verdict-${v.result.toLowerCase()}`}>
                <div className="verdict-head">
                  <VerdictPill v={v.result} />
                  <span className="inv">
                    {v.invariant_id}
                    <span className="muted">@{v.invariant_version}</span>
                  </span>
                </div>
                <div className="small">{v.explanation}</div>
                {v.witness.length > 0 && (
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
          <button className="btn" onClick={() => onOpenTimeline(sel.branch_id)} disabled={!selHasTrace} title={selHasTrace ? "" : "No trace exported for this branch (the pipeline exports root and failing branches)"}>
            Open timeline →
          </button>
        </aside>
      </div>
    </div>
  );
}

const METRICS: { key: keyof ComparisonRow; label: string; help: string; fmt: (v: number | null) => string }[] = [
  { key: "n_nodes", label: "nodes", help: "Branches actually run, root included (deduplicated by schedule hash; time-rule violations rejected).", fmt: (v) => (v == null ? "—" : String(v)) },
  { key: "n_failing", label: "failing", help: "Branches with a FAIL for an invariant that PASSes on the root trace.", fmt: (v) => (v == null ? "—" : String(v)) },
  { key: "failing_rate_per_100", label: "fail / 100", help: "100 * n_failing / max(1, n_nodes - 1).", fmt: (v) => (v == null ? "—" : v.toFixed(1)) },
  { key: "first_decision_fail_index", label: "first decision FAIL", help: "Index in exploration order (root = 0) of the first branch failing decision.consistent_with_truth; none if not found within budget.", fmt: (v) => (v == null ? "none" : `#${v}`) },
  { key: "wall_ms", label: "wall ms", help: "Wall-clock milliseconds for explore() on the machine that produced this file; not portable.", fmt: (v) => (v == null ? "—" : v.toFixed(0)) },
];

function ComparisonTable({ comparison, strategies, active, onPick }: { comparison: Record<string, ComparisonRow>; strategies: string[]; active: string; onPick: (s: string) => void }) {
  const rows = strategies.filter((s) => comparison[s]);
  if (!rows.length) return null;
  const best = (key: keyof ComparisonRow): string | null => {
    const vals = rows.map((s) => [s, comparison[s][key]] as const).filter(([, v]) => v != null) as (readonly [string, number])[];
    if (vals.length < 2) return null;
    const higherIsBetter = key === "n_failing" || key === "failing_rate_per_100";
    const sorted = [...vals].sort((a, b) => (higherIsBetter ? b[1] - a[1] : a[1] - b[1]));
    return sorted[0][1] === sorted[1][1] ? null : sorted[0][0];
  };
  return (
    <div className="panel compare-panel">
      <div className="card-head">
        <h2>Guided vs random</h2>
        <span className="small muted">same scenario, seed and budget; hover a column header for its definition</span>
      </div>
      <div className="table-wrap">
      <table className="table compare">
        <thead>
          <tr>
            <th>strategy</th>
            {METRICS.map((m) => (
              <th key={m.key} title={m.help} className="num">
                {m.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s} className={s === active ? "active" : ""} onClick={() => onPick(s)}>
              <td>
                <b>{s}</b>
              </td>
              {METRICS.map((m) => (
                <td key={m.key} className={`num ${best(m.key) === s ? "best" : ""}`}>
                  {m.fmt(comparison[s][m.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </div>
  );
}

function Stat({ label, value, tone, title }: { label: string; value: number; tone?: string; title?: string }) {
  return (
    <div className={`stat ${tone ? `stat-${tone}` : ""}`} title={title}>
      <div className="stat-v">{value}</div>
      <div className="stat-l">{label}</div>
    </div>
  );
}
