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

// ---------------------------------------------------------------- Inspect views: plain-English labels

/** Plain-English names for the invariants this project ships; the id stays visible as the secondary, technical label. */
const INVARIANT_NAME: Record<string, { name: string; help: string }> = {
  "decision.consistent_with_truth": { name: "Decision matches the truth", help: "Re-evaluating each decision rule over the true event log gives the same option the actor chose." },
  "absence.no_corrupted_in_decision": { name: "No decision on corrupted data", help: "No decision relied on an event whose payload was corrupted in transit." },
  "response.alert_ack": { name: "Alerts acknowledged in time", help: "Every alert is acknowledged by the responsible role within the allowed window." },
  "epistemic.no_future_knowledge": { name: "Nobody knows the future", help: "No role view or decision contains an event before that event was received." },
  "epistemic.label_isolation": { name: "Private data stays private", help: "A role never receives an event whose policy label it is not cleared to see." },
};

export function invariantName(id: string): string {
  if (INVARIANT_NAME[id]) return INVARIANT_NAME[id].name;
  const tail = id.split(".").pop() ?? id;
  const s = tail.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function invariantHelp(id: string): string {
  return INVARIANT_NAME[id]?.help ?? id;
}

export const OP_NAME: Record<string, string> = {
  delay: "Delay",
  drop: "Drop",
  duplicate: "Duplicate",
  reorder: "Reorder",
  corrupt: "Corrupt",
  revoke: "Cut off",
  clock_skew: "Clock skew",
};

export const OP_HELP: Record<string, string> = {
  delay: "Deliver a message later than the network would have.",
  drop: "The message never arrives.",
  duplicate: "The message arrives more than once.",
  reorder: "The message overtakes or falls behind its neighbours.",
  corrupt: "A field of the message is altered in transit.",
  revoke: "A role stops receiving anything from a given time.",
  clock_skew: "A source stamps its events with a wrong clock.",
};

export function opName(op: string): string {
  return OP_NAME[op] ?? humanName(op);
}

/** The event an intervention targets, in words: "the correction #001". */
function targetWords(iv: Intervention): string {
  const id = iv.target.event_id;
  if (id) return `the ${eventWord(id)} ${id.replace(/^.*?[-_#]/, "#")}`;
  if (iv.target.kind) return `a ${String(iv.target.kind).replace(/_/g, " ")}`;
  return "an event";
}

/** A fuller plain-English sentence than interventionSentence: covers every op and its role/field parameters. */
export function interventionPlain(iv: Intervention): string {
  const p = iv.params ?? {};
  const role = typeof p.role === "string" ? ` for ${humanName(p.role)}` : "";
  const ev = targetWords(iv);
  switch (iv.op) {
    case "delay":
      return `Delay ${ev} by ${humanDuration(Number(p.by_ns ?? 0))}${role}`;
    case "drop":
      return `Drop ${ev}${role}`;
    case "duplicate": {
      const c = Number(p.count ?? 1);
      return `Deliver ${ev} ${c === 1 ? "twice" : `${c + 1} times`}${role}`;
    }
    case "reorder":
      return `Move ${ev} out of order by ${humanDuration(Number(p.by_ns ?? 0))}${role}`;
    case "corrupt": {
      const field = humanName(String(p.field ?? "payload").replace(/^payload\./, ""));
      return `Corrupt the "${field}" field of ${ev}`;
    }
    case "revoke":
      return `Cut off ${humanName(String(p.role ?? "a role"))} from ${mmss(Number(p.from_ns ?? 0))}`;
    case "clock_skew":
      return `Skew ${humanName(String(p.source_id ?? "a source"))}'s clock by ${humanDuration(Number(p.offset_ns ?? 0))}`;
    default:
      return interventionText(iv);
  }
}

/** Order-insensitive key used to group branches whose schedules contain the same interventions. */
export function scheduleKey(s: Intervention[]): string {
  return s.map(interventionText).sort().join(" | ");
}

/** "3.6 ms" for small wall-cost numbers stored in ns. */
export function ms(ns: number): string {
  const v = ns / 1e6;
  return `${v >= 10 ? v.toFixed(0) : v.toFixed(1)} ms`;
}

const fmtNum = (v: unknown): string => (typeof v === "number" ? (Number.isInteger(v) ? String(v) : String(Math.round(v * 1000) / 1000)) : String(v));

/** One event payload summarised in words. Known kinds get a sentence; anything else falls back to "key value, key value". */
export function eventSummary(kind: string, payload: Record<string, unknown>, source?: string): string {
  const p = payload ?? {};
  const s = (k: string) => (p[k] == null ? "" : String(p[k]));
  const who = source ? humanName(source) : "Source";
  const cap = (x: string) => x.charAt(0).toUpperCase() + x.slice(1);
  switch (kind) {
    case "sighting":
      return `${cap(who)} reports ${s("note") || "a sighting"}${p.vessel ? ` (${s("vessel")})` : ""}, confidence ${fmtNum(p.confidence)}${p.bearing_deg != null ? `, bearing ${s("bearing_deg")}°` : ""}.`;
    case "threat_report":
      return `${cap(who)} reports ${s("note") || s("type") || "a threat"}${p.km_ahead != null ? ` ${s("km_ahead")} km ahead` : ""}, confidence ${fmtNum(p.confidence)}.`;
    case "correction":
      return `${cap(who)} corrects ${s("corrects")}: ${s("reason") || "the earlier report was wrong"}${p.confidence != null ? ` (confidence now ${fmtNum(p.confidence)})` : ""}.`;
    case "alert":
      return `${s("level") ? `${cap(s("level"))} alert` : "Alert"}: ${s("text") || "(no text)"}.`;
    case "ack":
      return `${cap(humanName(s("by") || who))} acknowledges ${s("acknowledges") || "an alert"}.`;
    case "status":
      return `Status${p.alert ? ` of ${s("alert")}` : ""}: ${s("state").replace(/_/g, " ") || "updated"}.`;
    case "ais_position":
      return `Vessel ${s("mmsi")} at ${fmtNum(p.lat)}, ${fmtNum(p.lon)}.`;
    case "tide":
      return `Tide ${s("height_m")} m, ${s("trend")}.`;
    case "weather":
      return `Visibility ${s("visibility_km")} km, wind ${s("wind_kt")} kt.`;
    case "berth_schedule":
      return `${s("vessel")} due at berth ${s("berth")}${p.eta_s != null ? ` in ${humanDuration(Number(p.eta_s) * NS)}` : ""}.`;
    case "crew_manifest":
      return `${s("vessel")} carries ${s("crew")} crew.`;
    case "patrol_status":
      return `${cap(who)} ${s("state").replace(/_/g, " ")} in sector ${s("sector")}.`;
    case "radar_contact":
      return `Radar track ${s("track")} at ${s("range_nm")} nm.`;
    case "shift_change":
      return `Shift change: ${humanName(s("outgoing"))} hands over to ${humanName(s("incoming"))}.`;
    case "lost_link":
      return `${cap(who)} lost its link${p.last_fix_km != null ? ` (last fix at km ${s("last_fix_km")})` : ""}.`;
    case "link_reacquired":
      return `${cap(who)} link restored after ${humanDuration(Number(p.outage_s ?? 0) * NS)}.`;
    case "order":
      return `Order: ${s("text")}.`;
    case "decision":
      return `${cap(humanName(s("actor")))} applies rule ${s("rule_id")} and chooses ${s("option")}, knowing ${Array.isArray(p.known_event_ids) ? p.known_event_ids.length : "?"} events.`;
    default: {
      const parts = Object.entries(p)
        .slice(0, 5)
        .map(([k, v]) => `${k.replace(/_/g, " ")} ${typeof v === "object" ? JSON.stringify(v) : fmtNum(v)}`);
      return parts.length ? `${cap(parts.join(", "))}.` : "No payload.";
    }
  }
}

/** Plain-English requirement names for release-policy claim patterns. */
export function requirementName(pattern: string): string {
  const known: Record<string, string> = {
    "tests:unit": "Unit tests pass",
    "invariant:*": "Every invariant holds on the baseline",
    "artifact:digest": "The artifact digest is pinned",
    "artifact:manifest": "The output manifest is recorded",
    "genome:challenge": "The failure genome was challenged",
    "minimize:one_minimal": "The counterexample is 1-minimal",
    "explore:guided_vs_random": "Guided and random exploration compared",
  };
  if (known[pattern]) return known[pattern];
  const s = humanName(pattern.replace(/[:*]+/g, " ").trim());
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** "counterexample:invariant:decision.consistent_with_truth" -> { group: "On the counterexample", name: "Decision matches the truth" } */
export function claimName(claim: string): { group: string; name: string } {
  if (claim.startsWith("counterexample:invariant:")) return { group: "Checked on the counterexample", name: invariantName(claim.slice("counterexample:invariant:".length)) };
  if (claim.startsWith("invariant:")) return { group: "Checked on the baseline", name: invariantName(claim.slice("invariant:".length)) };
  return { group: "Pipeline", name: requirementName(claim) };
}

function listWords(xs: string[], max = 3): string {
  const shown = xs.slice(0, max);
  const rest = xs.length - shown.length;
  if (rest > 0) return `${shown.join(", ")} and ${rest} more`;
  return shown.length > 1 ? `${shown.slice(0, -1).join(", ")} and ${shown[shown.length - 1]}` : shown.join("");
}

/** A verdict's explanation rewritten in plain English (raw nanosecond stamps and selector syntax removed). Falls back to the raw text. */
export function verdictSentence(v: { invariant_id: string; result: string; explanation: string }): string {
  const e = v.explanation;
  const capS = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
  const p = parseDecisionExplanation(e);
  if (p.chose && p.truth) {
    const miss = (e.match(/had not received \[([^\]]*)\]/)?.[1]?.match(/'([^']+)'/g) ?? []).map((s) => s.slice(1, -1));
    return `${capS(humanName(p.actor ?? "the decider"))} chose ${p.chose.replace(/_/g, " ")}, but the truth called for ${p.truth.replace(/_/g, " ")}${miss.length ? `; it had not yet received ${listWords(miss)}` : ""}.`;
  }
  const corr = e.match(/^decision decision:([^:]+):(\S+) relied on (\S+) matching/);
  if (corr) return `${capS(humanName(corr[1]))} decided ${humanName(corr[2])} relying on ${corr[3]}, which had arrived corrupted.`;
  const ack = e.match(/^alert (\S+) received by (\S+) at (\d+) had no ack by (\d+)$/);
  if (ack) return `${humanName(ack[2])} received ${ack[1]} at ${mmss(Number(ack[3]))} but had not acknowledged it by ${mmss(Number(ack[4]))}.`.replace(/^./, (c) => c.toUpperCase());
  return e.replace(/within (\d{9,})ns/g, (_, n) => `within ${humanDuration(Number(n))}`).replace(/as of (\d{9,})/g, (_, n) => `as of ${mmss(Number(n))}`);
}

/** The genome's abstract mechanism as a sentence a non-specialist can read. Null when the shape is not one we know. */
export function mechanismPlain(m: { op: string; selector: Record<string, unknown>; params: Record<string, unknown> }): string | null {
  const kind = String(m.selector.kind ?? "message").replace(/_/g, " ");
  const pos = m.selector.position === "last_before_decision" ? `the last ${kind} the decider receives before deciding` : `a ${kind} the decider receives`;
  const rel = m.selector.relation === "corrects_dependency" ? `, one that corrects a report the decision rule counts,` : "";
  if (m.op === "delay" && m.params.until === "after_decision") return `Hold back ${pos}${rel} until just after the decision.`;
  if (m.op === "delay") return `Delay ${pos}${rel}.`;
  if (m.op === "drop") return `Drop ${pos}${rel}.`;
  return null;
}
