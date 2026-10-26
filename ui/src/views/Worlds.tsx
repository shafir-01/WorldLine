import { useMemo, useState } from "react";
import type { BranchNode, ExplorationResult, DataIndex } from "../types";
import { cmp, interventionText, nodeStatus, secs, shortBranch, shortHash } from "../format";
import { ScheduleList } from "../components/Schedule";
import { VerdictPill } from "../components/Badges";

interface Props {
  exploration: ExplorationResult;
  index: DataIndex;
  selected: string;
  onSelect: (id: string) => void;
  onOpenTimeline: () => void;
}

interface Laid {
  node: BranchNode;
  depth: number;
  row: number;
  x: number;
  y: number;
}

const COL_W = 210;
const ROW_H = 30;
const PAD_X = 60;
const PAD_Y = 24;

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
  // children ordered by schedule text (stable, readable)
  for (const arr of children.values()) arr.sort((a, b) => cmp(a.schedule.map(interventionText).join("|"), b.schedule.map(interventionText).join("|")));

  const laid: Laid[] = [];
  let row = 0;
  const visit = (n: BranchNode, depth: number): number => {
    const kids = children.get(n.branch_id) ?? [];
    if (!kids.length) {
      const r = row++;
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
  const maxDepth = Math.max(0, ...laid.map((l) => l.depth));
  for (const l of laid) {
    l.x = PAD_X + l.depth * COL_W;
    l.y = PAD_Y + l.row * ROW_H;
  }
  return { laid, width: PAD_X * 2 + maxDepth * COL_W + 120, height: PAD_Y * 2 + Math.max(1, row) * ROW_H };
}

export default function Worlds({ exploration, index, selected, onSelect, onOpenTimeline }: Props) {
  const { laid, width, height } = useMemo(() => layout(exploration.nodes), [exploration]);
  const pos = useMemo(() => new Map(laid.map((l) => [l.node.branch_id, l])), [laid]);
  const [hover, setHover] = useState<{ id: string; x: number; y: number } | null>(null);
  const sel = exploration.nodes.find((n) => n.branch_id === selected) ?? exploration.nodes[0];
  const counts = useMemo(() => {
    const c = { PASS: 0, FAIL: 0, UNKNOWN: 0 };
    for (const n of exploration.nodes) c[nodeStatus(n)]++;
    return c;
  }, [exploration]);
  const hovered = hover ? exploration.nodes.find((n) => n.branch_id === hover.id) : undefined;

  return (
    <div className="view">
      <header className="view-head">
        <div>
          <h1>Worlds</h1>
          <p className="lede">
            Branch tree for <code>{exploration.scenario_id}</code> (seed {exploration.seed}, strategy <b>{exploration.strategy}</b>, budget {exploration.budget}). Root is the baseline world; each child applies one more
            intervention to its parent's fault schedule. Hover for the schedule, click to select.
          </p>
        </div>
        <div className="stat-row">
          <Stat label="branches" value={exploration.nodes.length} />
          <Stat label="FAIL" value={counts.FAIL} tone="fail" />
          <Stat label="PASS" value={counts.PASS} tone="pass" />
          <Stat label="UNKNOWN" value={counts.UNKNOWN} tone="unknown" />
        </div>
      </header>

      <div className="split">
        <div className="panel tree-panel" onMouseLeave={() => setHover(null)}>
          <div className="legend">
            <span><i className="dot dot-fail" /> FAIL (any invariant)</span>
            <span><i className="dot dot-pass" /> PASS</span>
            <span><i className="dot dot-unknown" /> UNKNOWN (missing signal)</span>
            <span><i className="dot dot-trace" /> trace file available</span>
          </div>
          <div className="tree-scroll">
            <svg className="tree" width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="branch tree">
              {laid.map((l) =>
                l.node.parent && pos.get(l.node.parent) ? (
                  <path
                    key={`link-${l.node.branch_id}`}
                    className="link"
                    d={(() => {
                      const p = pos.get(l.node.parent!)!;
                      const mx = (p.x + l.x) / 2;
                      return `M${p.x + 8},${p.y} C${mx},${p.y} ${mx},${l.y} ${l.x - 8},${l.y}`;
                    })()}
                  />
                ) : null,
              )}
              {laid.map((l) => {
                const st = nodeStatus(l.node);
                const isSel = l.node.branch_id === selected;
                const hasTrace = !!index.files.traces[l.node.branch_id];
                return (
                  <g
                    key={l.node.branch_id}
                    className={`node node-${st.toLowerCase()} ${isSel ? "node-selected" : ""}`}
                    transform={`translate(${l.x},${l.y})`}
                    onMouseEnter={(e) => setHover({ id: l.node.branch_id, x: e.clientX, y: e.clientY })}
                    onMouseMove={(e) => setHover({ id: l.node.branch_id, x: e.clientX, y: e.clientY })}
                    onClick={() => onSelect(l.node.branch_id)}
                    onDoubleClick={() => {
                      onSelect(l.node.branch_id);
                      onOpenTimeline();
                    }}
                    tabIndex={0}
                    onKeyDown={(e) => e.key === "Enter" && onSelect(l.node.branch_id)}
                    role="button"
                    aria-label={`${l.node.branch_id} ${st}`}
                  >
                    {isSel && <circle r="12" className="sel-ring" />}
                    <circle r="7" />
                    {hasTrace && <circle r="2.2" cy="-11" className="trace-dot" />}
                    <text x="12" y="4" className="node-label">
                      {shortBranch(l.node.branch_id)}
                      {l.node.schedule.length ? <tspan className="node-sub"> {l.node.schedule[l.node.schedule.length - 1].op}</tspan> : <tspan className="node-sub"> baseline</tspan>}
                    </text>
                  </g>
                );
              })}
            </svg>
          </div>
          {hover && hovered && (
            <div className="tooltip" style={{ left: hover.x + 14, top: hover.y + 14 }}>
              <div className="tt-title">
                <code>{hovered.branch_id}</code> <VerdictPill v={nodeStatus(hovered)} />
              </div>
              <ScheduleList schedule={hovered.schedule} />
              <div className="muted small">
                trace {shortHash(hovered.trace_digest)} · cost {(hovered.cost_ns / 1e6).toFixed(0)} ms
              </div>
            </div>
          )}
        </div>

        <aside className="panel side">
          <h2>Selected branch</h2>
          <code className="block">{sel.branch_id}</code>
          <dl className="kv">
            <dt>parent</dt>
            <dd>{sel.parent ?? "—"}</dd>
            <dt>trace digest</dt>
            <dd title={sel.trace_digest}>{shortHash(sel.trace_digest, 16)}</dd>
            <dt>cost</dt>
            <dd>{secs(sel.cost_ns)} wall</dd>
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
                    witness: {v.witness.map((w) => (
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
          <button className="btn" onClick={onOpenTimeline} disabled={!index.files.traces[sel.branch_id]} title={index.files.traces[sel.branch_id] ? "" : "No trace exported for this branch (only root and failing branches)"}>
            Open timeline →
          </button>
        </aside>
      </div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div className={`stat ${tone ? `stat-${tone}` : ""}`}>
      <div className="stat-v">{value}</div>
      <div className="stat-l">{label}</div>
    </div>
  );
}
