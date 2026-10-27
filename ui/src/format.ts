import type { BranchNode, Intervention, Selector, Verdict } from "./types";

export const NS = 1_000_000_000;

export function secs(ns: number): string {
  const s = ns / NS;
  if (Math.abs(s) >= 60 && Number.isInteger(s / 60)) return `${s / 60}m`;
  return `${Number.isInteger(s) ? s : s.toFixed(1)}s`;
}

export function tClock(ns: number): string {
  const s = Math.round(ns / NS);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `t+${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
}

export function shortHash(h: string, n = 10): string {
  const body = h.replace(/^sha256:/, "");
  return body.length > n ? `${body.slice(0, n)}…` : body;
}

export function shortBranch(id: string): string {
  if (id === "root") return "root";
  const [, h] = id.split("/");
  return `root/${(h ?? "").replace(/^sha256:/, "").slice(0, 7)}`;
}

export function selectorText(t: Selector): string {
  if (t.event_id) return `event ${t.event_id}`;
  const parts: string[] = [];
  if (t.kind) parts.push(`kind=${t.kind}`);
  if (t.role) parts.push(`role=${t.role}`);
  if (t.nth != null) parts.push(`nth=${t.nth}`);
  for (const [k, v] of Object.entries(t)) if (!["event_id", "kind", "role", "nth"].includes(k)) parts.push(`${k}=${String(v)}`);
  return parts.join(" ") || "(any)";
}

export function paramsText(iv: Intervention): string {
  const p = iv.params ?? {};
  return Object.entries(p)
    .map(([k, v]) => {
      if (typeof v === "number" && k.endsWith("_ns")) return `${k.replace(/_ns$/, "")}=${secs(v)}`;
      return `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`;
    })
    .join(", ");
}

export function interventionText(iv: Intervention): string {
  const p = paramsText(iv);
  return `${iv.op} ${selectorText(iv.target)}${p ? ` (${p})` : ""}`;
}

export function nodeStatus(n: BranchNode): Verdict {
  if (n.verdicts.some((v) => v.result === "FAIL")) return "FAIL";
  if (n.verdicts.some((v) => v.result === "UNKNOWN")) return "UNKNOWN";
  return "PASS";
}

export function cmp<T>(a: T, b: T): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

// ---------------------------------------------------------------- human-facing helpers (Story view, short ids)

/** Strips well-known prefixes (sha256:, genome:, ed25519:, root/) and returns the 7-char short id. */
export function shortId(h: string, n = 7): string {
  const body = h.replace(/^root\//, "").replace(/^(sha256|genome|ed25519):/, "");
  return body.slice(0, n);
}

/** "5 min", "3 min 30 s", "45 s" */
export function humanDuration(ns: number): string {
  const s = Math.round(Math.abs(ns) / NS);
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m && r) return `${m} min ${r} s`;
  if (m) return `${m} min`;
  return `${r} s`;
}

/** Minutes:seconds into the scenario, e.g. "23:27". */
export function mmss(ns: number): string {
  return tClock(ns).replace(/^t\+/, "");
}

const KIND_WORD: Record<string, string> = { ack: "acknowledgement", ais: "AIS position", tele: "telemetry", retract: "retraction" };

/** "correction-001" -> "correction" (the event kind word for prose). */
export function eventWord(id: string): string {
  const k = id.replace(/[-_#]\d+.*$/, "");
  return KIND_WORD[k] ?? k.replace(/_/g, " ");
}

/** One intervention as a plain-English instruction. */
export function interventionSentence(iv: Intervention): string {
  const ev = iv.target.event_id ? `the ${eventWord(iv.target.event_id)}` : `${iv.target.kind ?? "an"} event`;
  const by = typeof iv.params?.by_ns === "number" ? humanDuration(iv.params.by_ns as number) : null;
  switch (iv.op) {
    case "delay":
      return `Delay ${ev}${by ? ` by ${by}` : ""}`;
    case "duplicate": {
      const c = Number(iv.params?.count ?? 1);
      return `Send ${ev} ${c === 1 ? "twice" : `${c + 1} times`}`;
    }
    case "reorder":
      return `Shuffle ${ev} out of order${by ? ` (${by})` : ""}`;
    case "drop":
      return `Drop ${ev}`;
    case "corrupt":
      return `Corrupt ${ev}`;
    case "revoke":
      return `Revoke ${ev}`;
    case "clock_skew":
      return `Skew the clock${by ? ` by ${by}` : ""}`;
    default:
      return `${iv.op} ${ev}`;
  }
}

/** "exists_kind:correction:with_payload_field:corrects" -> "A correction event exists, with a 'corrects' field". */
export function preconditionText(p: string): string {
  const known: Record<string, string> = {
    has_decision_rule: "Someone decides by a rule",
    decision_rule_depends_on_a_corrected_kind: "The rule reads a kind of report that can be corrected",
    event_received_by_decision_role_before_decision: "The decider normally gets the correction in time",
  };
  if (known[p]) return known[p];
  const m = p.match(/^exists_kind:([^:]+):with_payload_field:(.+)$/);
  if (m) return `A ${m[1]} message exists that says what it ${m[2]}`;
  const s = p.replace(/[_:]+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Parses "... chose 'escalate' but truth as of ... gives 'monitor' ..." */
export function parseDecisionExplanation(text: string): { actor?: string; chose?: string; truth?: string } {
  const actor = text.match(/decision:([^:\s]+):/)?.[1];
  const chose = text.match(/chose '([^']+)'/)?.[1];
  const truth = text.match(/gives '([^']+)'/)?.[1];
  return { actor, chose, truth };
}

export function humanName(id: string): string {
  return id.replace(/_/g, " ");
}
