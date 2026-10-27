import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import type { BranchNode, ComparisonRow, LoadedData, VerdictRecord } from "../types";
import type { TraceEntry } from "../data";
import { cmp, sortStrategies, STRATEGY_HELP, interventionPlain, interventionText, invariantHelp, invariantName, ms, nodeStatus, opName, OP_HELP, scheduleKey, shortBranch, shortId, verdictSentence } from "../format";
import { ScheduleList } from "../components/Schedule";
import { VerdictGlyph, VerdictPill } from "../components/Badges";
import { ShortId } from "../components/ShortId";
import { Check, Chip, ContextBar, Ctx, Disclosure, keepScroll, Seg, Tip, ViewIntro } from "../components/Inspect";
import "../inspect.css";

interface Props {
  data: LoadedData;
  registry: Map<string, TraceEntry>;
  selected: string;
  onSelect: (id: string) => void;
  onOpenTimeline: (id?: string) => void;
}

type SortKey = "order" | "broke" | "changes" | "cost";
interface Member {
  node: BranchNode;
  strategy: string;
  order: number;
}
interface Group {
  key: string;
  members: Member[];
  lead: Member;
  invariants: string[];
  ops: string[];
}

export default function Worlds({ data, registry, selected, onSelect, onOpenTimeline }: Props) {
  const ex = data.exploration;
  const strategies = sortStrategies(ex.strategies.filter((s) => ex.runs[s]));
  const owner = strategies.find((s) => ex.runs[s].nodes.some((n) => n.branch_id === selected && n.branch_id !== "root"));
  const [strategy, setStrategy] = useState<string>(owner ?? strategies[0]);
  const [invOn, setInvOn] = useState<Set<string>>(new Set());
  const [opOn, setOpOn] = useState<Set<string>>(new Set());
  const [showPassing, setShowPassing] = useState(false);
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "order", dir: 1 });

  useEffect(() => {
    if (owner && strategy !== "both" && owner !== strategy) setStrategy(owner);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  const select = (id: string) => keepScroll(() => onSelect(id));
  const runsShown = strategy === "both" ? strategies : [strategy];

  // Every non-root branch of the shown runs, with its exploration order.
  const members = useMemo<Member[]>(
    () => runsShown.flatMap((s) => ex.runs[s].nodes.map((node, order) => ({ node, strategy: s, order })).filter((m) => m.node.branch_id !== "root")),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [strategy, ex],
  );
  const base = useMemo(() => members.filter((m) => showPassing || nodeStatus(m.node) !== "PASS"), [members, showPassing]);

  const invCounts = useMemo(() => countBy(base, (m) => m.node.failing_invariants), [base]);
  const opCounts = useMemo(() => countBy(base, (m) => [...new Set(m.node.schedule.map((iv) => iv.op))]), [base]);

  const groups = useMemo<Group[]>(() => {
    const filtered = base.filter(
      (m) => (!invOn.size || m.node.failing_invariants.some((i) => invOn.has(i))) && (!opOn.size || m.node.schedule.some((iv) => opOn.has(iv.op))),
    );
    const byKey = new Map<string, Member[]>();
    for (const m of filtered) {
      const k = scheduleKey(m.node.schedule);
      byKey.set(k, [...(byKey.get(k) ?? []), m]);
    }
    const out: Group[] = [...byKey.entries()].map(([key, ms]) => {
      const lead = ms.find((m) => m.node.branch_id === selected) ?? ms[0];
      return { key, members: ms, lead, invariants: [...new Set(ms.flatMap((m) => m.node.failing_invariants))].sort(), ops: [...new Set(lead.node.schedule.map((iv) => iv.op))] };
    });
    const val = (g: Group): number | string => {
      switch (sort.key) {
        case "broke":
          return g.invariants.map(invariantName).join(" ") || "~";
        case "changes":
          return g.lead.node.schedule.length;
        case "cost":
          return g.lead.node.cost_ns;
        default:
          return g.lead.order + (g.lead.strategy === strategies[0] ? 0 : 0.5);
      }
    };
    return out.sort((a, b) => sort.dir * cmp(val(a), val(b)) || a.lead.order - b.lead.order);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [base, invOn, opOn, sort, selected]);

  const allNodes = strategies.flatMap((s) => ex.runs[s].nodes.map((node) => ({ node, strategy: s })));
  const selMember = allNodes.find((m) => m.node.branch_id === selected) ?? allNodes[0];
  const sel = selMember.node;
  const selGroup = groups.find((g) => g.members.some((m) => m.node.branch_id === sel.branch_id));

  const nFail = members.filter((m) => nodeStatus(m.node) === "FAIL").length;
  const topInv = [...countBy(members, (m) => m.node.failing_invariants).entries()].sort((a, b) => b[1] - a[1])[0];
  const answer = (
    <>
      <b>
        {nFail} of {members.length}
      </b>{" "}
      {strategy === "both" ? "explored" : strategy} worlds broke at least one rule.
      {topInv && (
        <>
          {" "}
          The most common break is <b>{invariantName(topInv[0]).toLowerCase()}</b> ({topInv[1]}).
        </>
      )}{" "}
      Each row below is one changed world: what broke, and the changes that broke it.
    </>
  );

  const toggle = (set: Set<string>, setter: (s: Set<string>) => void, v: string) => {
    const n = new Set(set);
    if (n.has(v)) n.delete(v);
    else n.add(v);
    setter(n);
  };
  const nFiltered = groups.reduce((a, g) => a + g.members.length, 0);
  const filtersOn = invOn.size + opOn.size > 0;

  return (
    <div className="view ix-view">
      <ViewIntro eyebrow="Inspect · Worlds" question="Which worlds broke, and why?" answer={answer} />

      <ContextBar label="Worlds context">
        <Ctx k="Scenario">
          <code>{ex.scenario_id}</code> <span className="muted">seed {ex.seed}</span>
        </Ctx>
        <Seg
          label="Exploration strategy"
          value={strategy}
          onChange={setStrategy}
          options={[
            ...strategies.map((s) => ({ value: s, label: cap(s), sub: `${ex.runs[s].n_failing} broke`, title: STRATEGY_HELP[s] })),
            ...(strategies.length > 1 ? [{ value: "both", label: strategies.length > 2 ? "All" : "Both", sub: "merged", title: `${strategies.length > 2 ? "All" : "Both"} runs, identical schedules grouped` }] : []),
          ]}
        />
        <span className="ix-context-spacer" />
        <Ctx k="Selected">
          <VerdictGlyph v={nodeStatus(sel)} size={11} /> {sel.branch_id === "root" ? "baseline" : <code>{shortBranch(sel.branch_id)}</code>}
        </Ctx>
        <button className="btn btn-sm btn-primary" onClick={() => onOpenTimeline(sel.branch_id)} disabled={!registry.has(sel.branch_id)}>
          Open timeline
        </button>
      </ContextBar>

      <div className="ix-filters" role="group" aria-label="Filters">
        <div className="ix-filter-row">
          <span className="ix-filter-k">What broke</span>
          {[...invCounts.entries()]
            .sort((a, b) => b[1] - a[1])
            .map(([inv, n]) => (
              <Chip key={inv} on={invOn.has(inv)} count={n} onClick={() => toggle(invOn, setInvOn, inv)} title={`${inv}: ${invariantHelp(inv)}`}>
                {invariantName(inv)}
              </Chip>
            ))}
        </div>
        <div className="ix-filter-row">
          <span className="ix-filter-k">Change type</span>
          {[...opCounts.entries()]
            .sort((a, b) => b[1] - a[1])
            .map(([op, n]) => (
              <Chip key={op} on={opOn.has(op)} count={n} onClick={() => toggle(opOn, setOpOn, op)} title={`${op}: ${OP_HELP[op] ?? ""}`}>
                {opName(op)}
              </Chip>
            ))}
          <span className="ix-context-spacer" />
          <label className="ix-check-inline">
            <input type="checkbox" checked={showPassing} onChange={(e) => setShowPassing(e.target.checked)} /> Include worlds that held
          </label>
          {filtersOn && (
            <button className="btn btn-sm btn-ghost" onClick={() => (setInvOn(new Set()), setOpOn(new Set()))}>
              Clear filters
            </button>
          )}
        </div>
      </div>

      <div className="ix-split">
        <section className="panel ix-list-panel" aria-label="Worlds">
          <div className="ix-list-head">
            <span className="small muted" aria-live="polite">
              {nFiltered} world{nFiltered === 1 ? "" : "s"} in {groups.length} distinct schedule{groups.length === 1 ? "" : "s"}
              {filtersOn ? " (filtered)" : ""} · <kbd>↑</kbd> <kbd>↓</kbd> to move, <kbd>Enter</kbd> for the timeline
            </span>
          </div>
          <WorldTable groups={groups} sort={sort} setSort={setSort} selected={sel.branch_id} onSelect={select} onOpen={(id) => registry.has(id) && onOpenTimeline(id)} registry={registry} showStrategy={strategy === "both"} />
        </section>

        <aside className="panel ix-side" aria-label="Selected world">
          <SidePanel node={sel} strategy={selMember.strategy} group={selGroup} registry={registry} onOpenTimeline={onOpenTimeline} onSelect={select} />
        </aside>
      </div>

      <Disclosure summary="Branch tree" meta={`${strategy === "both" ? strategies[0] : strategy} run · the same worlds drawn as parent → child`}>
        <BranchTree nodes={ex.runs[strategy === "both" ? strategies[0] : strategy].nodes} selected={sel.branch_id} registry={registry} onSelect={select} onOpenTimeline={onOpenTimeline} />
      </Disclosure>
      <Disclosure summary={`${strategies.map(cap).join(" vs ")}, in numbers`} meta="same scenario, seed and budget">
        <ComparisonTable comparison={ex.comparison} strategies={strategies} active={strategy} onPick={setStrategy} />
      </Disclosure>
    </div>
  );
}

function countBy<T>(xs: T[], f: (x: T) => string[]): Map<string, number> {
  const m = new Map<string, number>();
  for (const x of xs) for (const k of f(x)) m.set(k, (m.get(k) ?? 0) + 1);
  return m;
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

// ---------------------------------------------------------------- list

function WorldTable({
  groups,
  sort,
  setSort,
  selected,
  onSelect,
  onOpen,
  registry,
  showStrategy,
}: {
  groups: Group[];
  sort: { key: SortKey; dir: 1 | -1 };
  setSort: (s: { key: SortKey; dir: 1 | -1 }) => void;
  selected: string;
  onSelect: (id: string) => void;
  onOpen: (id: string) => void;
  registry: Map<string, TraceEntry>;
  showStrategy: boolean;
}) {
  const rowRefs = useRef(new Map<string, HTMLTableRowElement>());
  const scrollRef = useRef<HTMLDivElement>(null);
  const selIdx = groups.findIndex((g) => g.members.some((m) => m.node.branch_id === selected));
  const focusIdx = selIdx >= 0 ? selIdx : 0;
  const onKey = (e: KeyboardEvent, i: number) => {
    const g = groups[i];
    if (e.key === "Enter") return onOpen(g.lead.node.branch_id);
    const d = e.key === "ArrowDown" ? 1 : e.key === "ArrowUp" ? -1 : e.key === "Home" ? -Infinity : e.key === "End" ? Infinity : 0;
    if (!d) return;
    e.preventDefault();
    const j = Math.max(0, Math.min(groups.length - 1, d === -Infinity ? 0 : d === Infinity ? groups.length - 1 : i + d));
    const id = groups[j].lead.node.branch_id;
    onSelect(id);
    requestAnimationFrame(() => rowRefs.current.get(groups[j].key)?.focus());
  };
  const th = (key: SortKey, label: string, cls = "") => (
    <th className={cls} aria-sort={sort.key === key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
      <button type="button" className="ix-sort" onClick={() => setSort({ key, dir: sort.key === key ? (sort.dir === 1 ? -1 : 1) : 1 })}>
        {label}
        <span className="ix-sort-i" aria-hidden>
          {sort.key === key ? (sort.dir === 1 ? "▲" : "▼") : "↕"}
        </span>
      </button>
    </th>
  );
  // Bring the selected row into view inside the list (not the page) when the list or the selection changes.
  const selKey = selIdx >= 0 ? groups[selIdx].key : null;
  useEffect(() => {
    const box = scrollRef.current;
    const row = selKey ? rowRefs.current.get(selKey) : null;
    if (!box || !row) return;
    const top = row.offsetTop - 40;
    if (top < box.scrollTop || row.offsetTop + row.offsetHeight > box.scrollTop + box.clientHeight) box.scrollTop = Math.max(0, top);
  }, [selKey]);
  if (!groups.length) return <div className="ix-empty">No world matches these filters.</div>;
  return (
    <div className="ix-table-scroll" ref={scrollRef}>
      <table className="ix-table" role="grid" aria-label="Worlds, one row per distinct schedule">
        <thead>
          <tr>
            {th("order", "#", "ix-col-n")}
            {th("broke", "What broke")}
            {th("changes", "What was changed")}
            {th("cost", "Run cost", "num")}
            <th className="ix-col-t">
              <span className="sr-only">Timeline available</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {groups.map((g, i) => {
            const n = g.lead.node;
            const st = nodeStatus(n);
            const isSel = i === selIdx;
            return (
              <tr
                key={g.key}
                ref={(el) => (el ? rowRefs.current.set(g.key, el) : rowRefs.current.delete(g.key))}
                aria-selected={isSel}
                tabIndex={i === focusIdx ? 0 : -1}
                className={`ix-row ${isSel ? "is-sel" : ""} st-${st.toLowerCase()}`}
                onClick={() => onSelect(n.branch_id)}
                onDoubleClick={() => onOpen(n.branch_id)}
                onKeyDown={(e) => onKey(e, i)}
              >
                <td className="ix-col-n">
                  <span className="ix-order">{g.lead.order}</span>
                  {showStrategy && <span className="ix-strat">{g.members.map((m) => m.strategy[0]).join("·")}</span>}
                </td>
                <td className="ix-broke">
                  {g.invariants.length ? (
                    g.invariants.map((inv) => (
                      <div key={inv} className="ix-inv">
                        <VerdictGlyph v="FAIL" size={10} />
                        <span>
                          {invariantName(inv)}
                          <code className="ix-term">{inv}</code>
                        </span>
                      </div>
                    ))
                  ) : (
                    <div className="ix-inv held">
                      <VerdictGlyph v={st} size={10} /> <span>{st === "UNKNOWN" ? "Could not tell" : "Nothing broke"}</span>
                    </div>
                  )}
                </td>
                <td>
                  <ol className="ix-changes">
                    {n.schedule.map((iv, k) => (
                      <li key={k}>
                        <span className={`ix-op op-${iv.op}`}>{opName(iv.op)}</span> {interventionPlain(iv)}
                      </li>
                    ))}
                  </ol>
                  {g.members.length > 1 && (
                    <div className="ix-same small muted">
                      same schedule in {g.members.length} worlds ({g.members.map((m) => `${m.strategy} #${m.order}`).join(", ")})
                    </div>
                  )}
                </td>
                <td className="num ix-cost">{ms(n.cost_ns)}</td>
                <td className="ix-col-t">
                  {registry.has(n.branch_id) ? (
                    <span className="ix-has-trace" title="Timeline available">
                      <svg width="16" height="12" viewBox="0 0 16 12" aria-hidden>
                        <path d="M1 3h14M1 9h14" stroke="currentColor" strokeWidth="1.2" opacity="0.5" />
                        <circle cx="5" cy="3" r="2" fill="currentColor" />
                        <circle cx="11" cy="9" r="2" fill="currentColor" />
                      </svg>
                      <span className="sr-only">timeline available</span>
                    </span>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------- side panel

function SidePanel({ node, strategy, group, registry, onOpenTimeline, onSelect }: { node: BranchNode; strategy: string; group?: Group; registry: Map<string, TraceEntry>; onOpenTimeline: (id?: string) => void; onSelect: (id: string) => void }) {
  const st = nodeStatus(node);
  const failing = node.verdicts.filter((v) => v.result !== "PASS");
  const passing = node.verdicts.filter((v) => v.result === "PASS");
  const hasTrace = registry.has(node.branch_id);
  const isRoot = node.branch_id === "root";
  const others = group?.members.filter((m) => m.node.branch_id !== node.branch_id) ?? [];
  return (
    <>
      <div className="ix-side-head">
        <span className="ix-eyebrow">Selected world</span>
        <h2>{isRoot ? "The baseline (nothing changed)" : st === "FAIL" ? `Broke ${failing.filter((v) => v.result === "FAIL").length} rule${failing.length === 1 ? "" : "s"}` : st === "UNKNOWN" ? "Could not be judged" : "Held: every rule passed"}</h2>
        <div className="small muted">
          {isRoot ? "baseline" : <code>{shortBranch(node.branch_id)}</code>} · {strategy} run · ran in {ms(node.cost_ns)}
        </div>
      </div>

      <h3 className="ix-h">What was changed</h3>
      {node.schedule.length ? (
        <ol className="ix-changes ix-changes-lg">
          {node.schedule.map((iv, k) => (
            <li key={k}>
              <span className={`ix-op op-${iv.op}`}>{opName(iv.op)}</span> {interventionPlain(iv)}
            </li>
          ))}
        </ol>
      ) : (
        <p className="small muted">Nothing: this is the recorded hour as it happened.</p>
      )}

      <h3 className="ix-h">Verdicts</h3>
      <ul className="ix-verdicts">
        {failing.map((v) => (
          <VerdictItem key={v.invariant_id} v={v} />
        ))}
      </ul>
      {passing.length > 0 && (
        <Disclosure summary={`${passing.length} other rule${passing.length === 1 ? "" : "s"} held`} defaultOpen={failing.length === 0}>
          <ul className="ix-checklist">
            {passing.map((v) => (
              <Check key={v.invariant_id} ok label={invariantName(v.invariant_id)} term={v.invariant_id} help={v.explanation} />
            ))}
          </ul>
        </Disclosure>
      )}

      <div className="ix-actions">
        <button className="btn btn-primary" onClick={() => onOpenTimeline(node.branch_id)} disabled={!hasTrace}>
          Open timeline →
        </button>
        {!hasTrace && <span className="small muted">No trace was exported for this world (only the baseline and failing worlds are).</span>}
      </div>

      {others.length > 0 && (
        <p className="small muted">
          Same schedule also explored as{" "}
          {others.map((m, i) => (
            <span key={m.node.branch_id}>
              {i > 0 && ", "}
              <button className="link" onClick={() => onSelect(m.node.branch_id)}>
                {m.strategy} #{m.order}
              </button>
            </span>
          ))}
          .
        </p>
      )}

      <Disclosure summary="Technical details">
        <dl className="kv">
          <dt>branch</dt>
          <dd>{isRoot ? "root" : <ShortId value={node.branch_id} />}</dd>
          <dt>parent</dt>
          <dd>{node.parent ? node.parent === "root" ? "root" : <ShortId value={node.parent} /> : "none"}</dd>
          <dt>trace digest</dt>
          <dd>
            <ShortId value={node.trace_digest} />
          </dd>
          <dt>cost_ns</dt>
          <dd>{node.cost_ns.toLocaleString()}</dd>
        </dl>
        <h5>Fault schedule (as recorded)</h5>
        <ScheduleList schedule={node.schedule} />
      </Disclosure>
    </>
  );
}

function VerdictItem({ v }: { v: VerdictRecord }) {
  return (
    <li className={`ix-verdict v-${v.result.toLowerCase()}`}>
      <div className="ix-verdict-head">
        <VerdictPill v={v.result} />
        <b>{invariantName(v.invariant_id)}</b>
        <Tip text={invariantHelp(v.invariant_id)} label={`About ${v.invariant_id}`} />
      </div>
      <code className="ix-term">
        {v.invariant_id}@{v.invariant_version}
      </code>
      <p className="small" title={v.explanation}>
        {verdictSentence(v)}
      </p>
      {v.witness.length > 0 && (
        <div className="ix-witness small">
          <span className="muted">Witness</span>
          {v.witness.map((w) => (
            <code key={w} className="wit">
              {w}
            </code>
          ))}
        </div>
      )}
      {v.missing_data.length > 0 && <div className="small muted">Missing signal: {v.missing_data.join(", ")}</div>}
    </li>
  );
}

// ---------------------------------------------------------------- tree (secondary visual)

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

function BranchTree({ nodes, selected, registry, onSelect, onOpenTimeline }: { nodes: BranchNode[]; selected: string; registry: Map<string, TraceEntry>; onSelect: (id: string) => void; onOpenTimeline: (id?: string) => void }) {
  const { laid, width, height } = useMemo(() => layout(nodes), [nodes]);
  const pos = useMemo(() => new Map(laid.map((l) => [l.node.branch_id, l])), [laid]);
  const [hover, setHover] = useState<{ id: string; x: number; y: number } | null>(null);
  const hovered = hover ? nodes.find((n) => n.branch_id === hover.id) : undefined;
  return (
    <div className="tree-panel" onMouseLeave={() => setHover(null)}>
      <div className="legend">
        <span>
          <i className="dot dot-fail" /> broke a rule
        </span>
        <span>
          <i className="dot dot-pass" /> held
        </span>
        <span>
          <i className="dot dot-unknown" /> could not tell (missing signal)
        </span>
        <span>
          <i className="dot dot-trace" /> timeline available
        </span>
        <span className="muted">Click to select, double-click for the timeline.</span>
      </div>
      <div className="tree-scroll ix-tree-scroll">
        <svg className="tree" width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Branch tree; the list above has the same worlds in an accessible form">
          {laid.map((l) =>
            l.node.parent && pos.get(l.node.parent) ? (
              <path
                key={`link-${l.node.branch_id}`}
                className="link"
                d={(() => {
                  const p = pos.get(l.node.parent!)!;
                  const mx = p.x + COL_W * 0.55;
                  return `M${p.x + 8},${p.y} C${mx},${p.y} ${mx},${l.y} ${l.x - 8},${l.y}`;
                })()}
              />
            ) : null,
          )}
          {laid.map((l) => {
            const st = nodeStatus(l.node);
            const isSel = l.node.branch_id === selected;
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
              >
                {isSel && <circle r="11" className="sel-ring" />}
                <circle r="6.5" />
                {registry.has(l.node.branch_id) && <circle r="2.2" cy="-10" className="trace-dot" />}
                <text x="11" y="4" className="node-label">
                  {l.node.branch_id === "root" ? "baseline" : shortBranch(l.node.branch_id).replace("root/", "")}
                  <tspan className="node-sub"> {fails.length ? fails.map(invariantName).join(", ") : l.node.schedule.map((iv) => opName(iv.op)).join(" + ")}</tspan>
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      {hover && hovered && (
        <div className="tooltip" style={{ left: hover.x + 14, top: hover.y + 14 }}>
          <div className="tt-title">
            <VerdictPill v={nodeStatus(hovered)} /> <code>{shortId(hovered.branch_id)}</code>
          </div>
          <ol className="ix-changes">
            {hovered.schedule.map((iv, k) => (
              <li key={k}>{interventionPlain(iv)}</li>
            ))}
          </ol>
          {hovered.failing_invariants?.length > 0 && <div className="small bad">Broke: {hovered.failing_invariants.map(invariantName).join(", ")}</div>}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- comparison

const METRICS: { key: keyof ComparisonRow; label: string; help: string; fmt: (v: number | null) => string }[] = [
  { key: "n_nodes", label: "Worlds run", help: "n_nodes: branches actually run, root included (deduplicated by schedule hash; time-rule violations rejected).", fmt: (v) => (v == null ? "—" : String(v)) },
  { key: "n_failing", label: "Broke a rule", help: "n_failing: branches with a FAIL for an invariant that PASSes on the root trace.", fmt: (v) => (v == null ? "—" : String(v)) },
  { key: "failing_rate_per_100", label: "Broke per 100", help: "failing_rate_per_100 = 100 × n_failing / max(1, n_nodes − 1).", fmt: (v) => (v == null ? "—" : v.toFixed(1)) },
  { key: "first_decision_fail_index", label: "First wrong decision at", help: "first_decision_fail_index: exploration order (root = 0) of the first branch failing decision.consistent_with_truth; none if not found within budget.", fmt: (v) => (v == null ? "never" : `#${v}`) },
  { key: "wall_ms", label: "Wall time", help: "wall_ms: wall-clock milliseconds for explore() on the machine that produced this file; not portable.", fmt: (v) => (v == null ? "—" : `${v.toFixed(0)} ms`) },
];

function ComparisonTable({ comparison, strategies, active, onPick }: { comparison: Record<string, ComparisonRow>; strategies: string[]; active: string; onPick: (s: string) => void }) {
  const rows = strategies.filter((s) => comparison[s]);
  if (!rows.length) return null;
  return (
    <div className="table-wrap">
      <table className="table ix-compare">
        <thead>
          <tr>
            <th>Strategy</th>
            {METRICS.map((m) => (
              <th key={m.key} className="num">
                <span className="ix-th-tip">
                  {m.label} <Tip text={m.help} label={`Definition of ${m.label}`} />
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s} className={s === active ? "active" : ""}>
              <td>
                <button className="link ix-link-plain" onClick={() => onPick(s)}>
                  {cap(s)}
                </button>
              </td>
              {METRICS.map((m) => (
                <td key={m.key} className="num">
                  {m.fmt(comparison[s][m.key])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
