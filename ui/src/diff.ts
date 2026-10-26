import type { Trace, WEvent } from "./types";

export interface MovedEvent {
  role: string;
  id: string;
  kind: string;
  from_ns: number;
  to_ns: number;
  delta_ns: number;
}
export interface DecisionDiff {
  rule_id: string;
  actor: string;
  at_ns: number;
  option_a: string | null;
  option_b: string | null;
  known_only_a: string[];
  known_only_b: string[];
}
export interface CausalDiff {
  only_in_a: { role: string; event: WEvent }[];
  only_in_b: { role: string; event: WEvent }[];
  moved: MovedEvent[];
  decisions: DecisionDiff[];
  corrupted: { role: string; id: string }[];
}

/** Mirrors explorer.causal_diff: events only in a / only in b, received-time deltas, decisions that differ. a = baseline, b = branch. */
export function causalDiff(a: Trace, b: Trace): CausalDiff {
  const out: CausalDiff = { only_in_a: [], only_in_b: [], moved: [], decisions: [], corrupted: [] };
  const roles = new Set([...Object.keys(a.views), ...Object.keys(b.views)]);
  for (const role of roles) {
    const ea = new Map((a.views[role]?.events ?? []).map((e) => [e.id, e]));
    const eb = new Map((b.views[role]?.events ?? []).map((e) => [e.id, e]));
    for (const [id, e] of ea) if (!eb.has(id)) out.only_in_a.push({ role, event: e });
    for (const [id, e] of eb) {
      const x = ea.get(id);
      if (!x) {
        out.only_in_b.push({ role, event: e });
        continue;
      }
      if (x.received_time_ns !== e.received_time_ns && x.received_time_ns != null && e.received_time_ns != null)
        out.moved.push({ role, id, kind: e.kind, from_ns: x.received_time_ns, to_ns: e.received_time_ns, delta_ns: e.received_time_ns - x.received_time_ns });
      if (JSON.stringify(x.payload) !== JSON.stringify(e.payload)) out.corrupted.push({ role, id });
    }
  }
  const da = new Map(a.decisions.map((d) => [String(d.payload.rule_id), d]));
  const db = new Map(b.decisions.map((d) => [String(d.payload.rule_id), d]));
  for (const rule of new Set([...da.keys(), ...db.keys()])) {
    const x = da.get(rule);
    const y = db.get(rule);
    const ka = new Set((x?.payload.known_event_ids as string[] | undefined) ?? []);
    const kb = new Set((y?.payload.known_event_ids as string[] | undefined) ?? []);
    const oa = (x?.payload.option as string | undefined) ?? null;
    const ob = (y?.payload.option as string | undefined) ?? null;
    const onlyA = [...ka].filter((k) => !kb.has(k));
    const onlyB = [...kb].filter((k) => !ka.has(k));
    if (oa !== ob || onlyA.length || onlyB.length)
      out.decisions.push({ rule_id: rule, actor: String((x ?? y)!.payload.actor), at_ns: Number((x ?? y)!.payload.at_ns), option_a: oa, option_b: ob, known_only_a: onlyA, known_only_b: onlyB });
  }
  out.moved.sort((m, n) => Math.abs(n.delta_ns) - Math.abs(m.delta_ns));
  return out;
}
