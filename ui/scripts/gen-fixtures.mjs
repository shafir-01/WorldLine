// Deterministic generator for SYNTHETIC fixtures that follow docs/ARCHITECTURE.md shapes.
// Run: node scripts/gen-fixtures.mjs   (writes into ui/fixtures)
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const OUT = join(here, "..", "fixtures");
rmSync(OUT, { recursive: true, force: true });
mkdirSync(join(OUT, "traces"), { recursive: true });
mkdirSync(join(OUT, "verdicts"), { recursive: true });

const S = 1_000_000_000; // ns per second
function sortKeys(o) {
  if (Array.isArray(o)) return o.map(sortKeys);
  if (o && typeof o === "object") return Object.fromEntries(Object.keys(o).sort().map((k) => [k, sortKeys(o[k])]));
  return o;
}
const canon = (o) => JSON.stringify(sortKeys(o));
const sha = (o) => "sha256:" + createHash("sha256").update(canon(o)).digest("hex");
const short = (o) => createHash("sha256").update(canon(o)).digest("hex").slice(0, 12);

// small deterministic PRNG (mulberry32)
function rng(seedStr) {
  let a = parseInt(createHash("sha256").update(seedStr).digest("hex").slice(0, 8), 16);
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const SCENARIO = "port_alert";
const SEED = 42;
const EPOCH = 0;
const HORIZON = 3600 * S;

const ROLES = {
  harbour_ops: { base_latency_ns: 20 * S, jitter_ns: 10 * S, drop_probability: 0, ordering: "fifo", visibility: ["public", "harbour_private"] },
  coast_guard: { base_latency_ns: 60 * S, jitter_ns: 30 * S, drop_probability: 0, ordering: "network", visibility: ["public"] },
  evaluator: { base_latency_ns: 5 * S, jitter_ns: 0, drop_probability: 0, ordering: "fifo", visibility: ["public", "harbour_private"] },
};

// ---- truth events (shared by every branch; interventions only affect delivery) ----
let n = 0;
const ev = (t, source, kind, payload, labels = ["public"], extra = {}) => ({
  id: `e-${String(++n).padStart(4, "0")}`,
  scenario_id: SCENARIO,
  source_id: source,
  event_time_ns: t * S,
  kind,
  payload,
  received_time_ns: null,
  geometry: extra.geometry ?? null,
  unit: extra.unit ?? null,
  quality: extra.quality ?? null,
  policy_labels: labels,
  provenance_ids: extra.provenance_ids ?? [],
  schema_version: "event.v1",
  priority: extra.priority ?? 0,
});

const truth = [
  ev(120, "radar.north", "sighting", { vessel: "unknown-1", confidence: 0.45, heading_deg: 210 }, ["public"], { geometry: { type: "Point", coordinates: [10.72, 59.91] } }),
  ev(300, "radar.north", "sighting", { vessel: "unknown-1", confidence: 0.72, heading_deg: 205 }, ["public"], { geometry: { type: "Point", coordinates: [10.7, 59.9] } }),
  ev(420, "ais.gateway", "sighting", { vessel: "unknown-1", confidence: 0.81, heading_deg: 200 }, ["public"], { geometry: { type: "Point", coordinates: [10.69, 59.89] } }),
  ev(480, "berth.sensor.7", "capacity", { berth: 7, free_slots: 1 }, ["harbour_private"], { unit: "slots" }),
  ev(540, "ais.gateway", "status", { gateway: "ais.gateway", healthy: true }, ["public"]),
  ev(600, "harbour_ops.actor", "alert", { level: "amber", reason: "two_high_confidence_sightings", vessel: "unknown-1" }, ["public"], { priority: 5 }),
  ev(780, "coast_guard.actor", "acknowledged", { alert_ref: "e-0006", by: "cg.watch" }, ["public"]),
  ev(900, "berth.sensor.7", "capacity", { berth: 7, free_slots: 0 }, ["harbour_private"], { unit: "slots" }),
  ev(1020, "radar.north", "sighting", { vessel: "unknown-1", confidence: 0.3, heading_deg: 95 }, ["public"], { geometry: { type: "Point", coordinates: [10.74, 59.92] } }),
  ev(1250, "ais.gateway", "correction", { corrects: "e-0003", vessel: "unknown-1", confidence: 0.12, note: "AIS transponder misread; vessel is a pilot boat" }, ["public"], { priority: 7, provenance_ids: ["e-0003"] }),
  ev(1800, "weather.svc", "weather", { wind_kt: 18, visibility_m: 6000 }, ["public"], { unit: "kt" }),
  ev(2100, "radar.north", "sighting", { vessel: "pilot-3", confidence: 0.92, heading_deg: 10 }, ["public"]),
  ev(2700, "harbour_ops.actor", "alert", { level: "green", reason: "stand_down", vessel: "unknown-1" }, ["public"], { priority: 5 }),
  ev(2880, "coast_guard.actor", "acknowledged", { alert_ref: "e-0013", by: "cg.watch" }, ["public"]),
];

const DECISION_RULES = [
  { id: "raise_alert", actor: "harbour_ops.actor", role: "harbour_ops", at_ns: 600 * S, window_ns: 600 * S, when: "count(kind='sighting', payload.confidence>=0.6) >= 2", then: "raise_amber_alert", otherwise: "monitor" },
  { id: "dispatch_cutter", actor: "coast_guard.actor", role: "coast_guard", at_ns: 1500 * S, window_ns: 1500 * S, when: "exists(kind='alert', payload.level='amber') and not exists(kind='correction')", then: "dispatch_cutter", otherwise: "hold" },
  { id: "stand_down", actor: "harbour_ops.actor", role: "harbour_ops", at_ns: 2700 * S, window_ns: 2700 * S, when: "exists(kind='correction') or count(kind='sighting', payload.confidence<0.4) >= 1", then: "stand_down", otherwise: "keep_alert" },
];

function hashLog(events) {
  let prev = "sha256:genesis";
  for (const e of events) prev = "sha256:" + createHash("sha256").update(prev + sha(e)).digest("hex");
  return prev;
}
const log = (events) => ({ events, digest: hashLog(events) });
const schedHash = (sched) => (sched.length ? short(sched) : "0".repeat(12));

function nthOf(e) {
  let k = 0;
  for (const t of truth) {
    if (t.kind === e.kind) { k++; if (t.id === e.id) return k; }
  }
  return -1;
}

// ---- delivery with interventions ----
function deliver(schedule) {
  const views = {};
  for (const [role, pol] of Object.entries(ROLES)) {
    const r = rng(`deliver/${role}/${SEED}`);
    const out = [];
    for (const e of truth) {
      if (!e.policy_labels.some((l) => pol.visibility.includes(l))) continue;
      const jitter = pol.jitter_ns ? Math.floor(r() * pol.jitter_ns) : 0;
      let recv = e.event_time_ns + pol.base_latency_ns + jitter;
      let payload = e.payload;
      let quality = e.quality;
      let prov = e.provenance_ids;
      let drop = false;
      let dup = 0;
      for (const iv of schedule) {
        const t = iv.target;
        const matchesRole = !t.role || t.role === role;
        const matches = (t.event_id && t.event_id === e.id) || (t.kind && t.kind === e.kind && matchesRole && (t.nth == null || nthOf(e) === t.nth)) || (!t.event_id && !t.kind && matchesRole);
        if (!matches) continue;
        if (iv.op === "delay") recv += iv.params.by_ns;
        if (iv.op === "drop") drop = true;
        if (iv.op === "duplicate") dup = iv.params.count;
        if (iv.op === "corrupt") { payload = { ...payload, [iv.params.field]: iv.params.value }; quality = 0.2; prov = [e.id]; }
        if (iv.op === "reorder") recv += 90 * S;
        if (iv.op === "clock_skew" && iv.params.source_id === e.source_id) recv += iv.params.offset_ns;
        if (iv.op === "revoke" && iv.params.role === role && e.event_time_ns >= iv.params.from_ns) drop = true;
      }
      if (drop) continue;
      out.push({ ...e, received_time_ns: recv, payload, quality, provenance_ids: prov });
      for (let k = 0; k < dup; k++) out.push({ ...e, id: `${e.id}#dup${k + 1}`, received_time_ns: recv + (k + 1) * 15 * S, provenance_ids: [e.id] });
    }
    out.sort((a, b) => a.received_time_ns - b.received_time_ns || a.id.localeCompare(b.id));
    views[role] = out;
  }
  return views;
}

// evaluate decision rules over a role's known events
function evalRule(rule, known) {
  const inWindow = known.filter((e) => e.received_time_ns <= rule.at_ns && e.received_time_ns >= rule.at_ns - rule.window_ns);
  const kinds = (k, pred = () => true) => inWindow.filter((e) => e.kind === k && pred(e));
  let ok = false;
  if (rule.id === "raise_alert") ok = kinds("sighting", (e) => e.payload.confidence >= 0.6).length >= 2;
  if (rule.id === "dispatch_cutter") ok = kinds("alert", (e) => e.payload.level === "amber").length > 0 && kinds("correction").length === 0;
  if (rule.id === "stand_down") ok = kinds("correction").length > 0 || kinds("sighting", (e) => e.payload.confidence < 0.4).length >= 1;
  return { option: ok ? rule.then : rule.otherwise, known: inWindow.map((e) => e.id) };
}
function evalRuleOnTruth(rule) {
  const known = truth.filter((e) => e.event_time_ns <= rule.at_ns).map((e) => ({ ...e, received_time_ns: e.event_time_ns }));
  return evalRule(rule, known);
}

// ---- invariants ----
const INVARIANTS = [
  { id: "alert_acknowledged_within_300s", version: "1.0", kind: "response", description: "every amber/green alert in the coast_guard view is acknowledged within 300s" },
  { id: "coast_guard_never_sees_harbour_private", version: "1.0", kind: "absence", description: "role coast_guard never receives an event labelled harbour_private" },
  { id: "information_state_separation", version: "1.2", kind: "epistemic", description: "no decision cites an event its role had not received at at_ns" },
  { id: "decision_matches_truth", version: "1.1", kind: "decision", description: "each decision equals the option its rule yields over truth as of at_ns" },
  { id: "berth_capacity_non_negative", version: "1.0", kind: "always", description: "no capacity reading is negative" },
];
const V = (invariant_id, invariant_version, result, witness, explanation, missing_data) => ({ invariant_id, invariant_version, result, witness, explanation, missing_data });

function checkAll(trace) {
  const derived_at_ns = 7_000_000 + Math.floor(rng(trace.digest)() * 2_000_000);
  const out = [];
  const cg = trace.views.coast_guard.events;
  {
    const alerts = cg.filter((e) => e.kind === "alert");
    const bad = alerts.filter((a) => !cg.some((k) => k.kind === "acknowledged" && k.payload.alert_ref === a.id.split("#")[0] && k.received_time_ns - a.received_time_ns <= 300 * S));
    if (alerts.length === 0) out.push(V("alert_acknowledged_within_300s", "1.0", "UNKNOWN", [], "coast_guard view contains no 'alert' events; response invariant has nothing to evaluate", ["alert"]));
    else out.push(V("alert_acknowledged_within_300s", "1.0", bad.length ? "FAIL" : "PASS", bad.map((b) => b.id), bad.length ? `${bad.length} alert(s) not acknowledged within 300s in coast_guard view` : `${alerts.length} alert(s) acknowledged within 300s in coast_guard view`, []));
  }
  {
    const bad = cg.filter((e) => e.policy_labels.includes("harbour_private"));
    out.push(V("coast_guard_never_sees_harbour_private", "1.0", bad.length ? "FAIL" : "PASS", bad.map((b) => b.id), bad.length ? "coast_guard view contains harbour_private labelled events" : "no harbour_private event in coast_guard view", []));
  }
  {
    const bad = [];
    for (const d of trace.decisions) {
      const role = DECISION_RULES.find((r) => r.id === d.payload.rule_id).role;
      const known = new Set(trace.views[role].events.filter((e) => e.received_time_ns <= d.payload.at_ns).map((e) => e.id));
      for (const id of d.payload.known_event_ids) if (!known.has(id)) bad.push(id);
    }
    out.push(V("information_state_separation", "1.2", bad.length ? "FAIL" : "PASS", bad, bad.length ? "a decision cites events its role had not received" : "every decision cites only events received by its role at at_ns", []));
  }
  {
    const witness = [];
    const notes = [];
    for (const d of trace.decisions) {
      const rule = DECISION_RULES.find((r) => r.id === d.payload.rule_id);
      const truthRes = evalRuleOnTruth(rule);
      if (truthRes.option !== d.payload.option) {
        const knownSet = new Set(d.payload.known_event_ids);
        const missing = truth
          .filter((e) => e.event_time_ns <= rule.at_ns && !knownSet.has(e.id) && e.policy_labels.some((l) => ROLES[rule.role].visibility.includes(l)))
          .map((e) => e.id);
        witness.push(...missing);
        notes.push(`${rule.id}: chose '${d.payload.option}', truth-as-of-at_ns yields '${truthRes.option}'; missing ${missing.join(",")}`);
      }
    }
    out.push(V("decision_matches_truth", "1.1", witness.length ? "FAIL" : "PASS", witness, witness.length ? notes.join("; ") : "all 3 decisions equal the option computed over truth as of at_ns", []));
  }
  {
    const caps = truth.filter((e) => e.kind === "capacity");
    const bad = caps.filter((e) => e.payload.free_slots < 0);
    out.push(V("berth_capacity_non_negative", "1.0", caps.length === 0 ? "UNKNOWN" : bad.length ? "FAIL" : "PASS", bad.map((b) => b.id), caps.length === 0 ? "no capacity signal present" : "all capacity readings >= 0", caps.length === 0 ? ["capacity"] : []));
  }
  return out.map((v) => ({ ...v, derived_at_ns, trace_digest: trace.digest }));
}

function runBranch(schedule) {
  const branch_id = schedule.length ? `root/${schedHash(schedule)}` : "root";
  const views = deliver(schedule);
  const decisions = DECISION_RULES.map((rule, i) => {
    const res = evalRule(rule, views[rule.role]);
    return {
      id: `d-${String(i + 1).padStart(3, "0")}`,
      scenario_id: SCENARIO,
      source_id: rule.actor,
      event_time_ns: rule.at_ns,
      kind: "decision",
      payload: { actor: rule.actor, rule_id: rule.id, option: res.option, known_event_ids: res.known, at_ns: rule.at_ns },
      received_time_ns: rule.at_ns,
      geometry: null,
      unit: null,
      quality: null,
      policy_labels: ["public"],
      provenance_ids: res.known,
      schema_version: "event.v1",
      priority: 0,
    };
  });
  const viewLogs = Object.fromEntries(Object.entries(views).map(([r, evs]) => [r, log(evs)]));
  const truthLog = log(truth);
  const digest = sha({ truth: truthLog.digest, views: Object.values(viewLogs).map((v) => v.digest).sort(), decisions: decisions.map(sha) });
  const trace = {
    synthetic: true,
    scenario_id: SCENARIO,
    seed: SEED,
    branch_id,
    fault_schedule: schedule,
    fault_schedule_hash: schedHash(schedule),
    truth: truthLog,
    views: viewLogs,
    decisions,
    clock_end: { epoch_ns: EPOCH, tick_ns: 1 * S, now_ns: HORIZON, horizon_ns: HORIZON },
    roles: Object.fromEntries(Object.entries(ROLES).map(([r, p]) => [r, { base_latency_ns: p.base_latency_ns, jitter_ns: p.jitter_ns, drop_probability: p.drop_probability, ordering: p.ordering, visibility: p.visibility }])),
    decision_rules: DECISION_RULES,
    digest,
  };
  return { trace, verdicts: checkAll(trace) };
}

// ---- exploration tree ----
const I = (op, target, params) => ({ op, target, params });
const corr = { kind: "correction", role: "coast_guard", nth: 1 };
const sight2 = { kind: "sighting", role: "coast_guard", nth: 2 };
const alert1 = { kind: "alert", role: "coast_guard", nth: 1 };
const schedules = [
  // level 1 (children of root)
  [I("delay", sight2, { by_ns: 120 * S })],
  [I("delay", alert1, { by_ns: 60 * S })],
  [I("drop", { kind: "weather", role: "harbour_ops", nth: 1 }, {})],
  [I("duplicate", sight2, { count: 2 })],
  [I("reorder", { kind: "sighting", role: "coast_guard", nth: 3 }, {})],
  [I("corrupt", { kind: "sighting", role: "harbour_ops", nth: 1 }, { field: "confidence", value: 0.65 })],
  [I("clock_skew", { event_id: "e-0011" }, { source_id: "weather.svc", offset_ns: 45 * S })],
  [I("delay", corr, { by_ns: 400 * S })], // FAIL (minimal)
  [I("revoke", { role: "coast_guard" }, { role: "coast_guard", from_ns: 500 * S })], // UNKNOWN: coast_guard view loses every alert
  [I("delay", { kind: "acknowledged", role: "harbour_ops", nth: 1 }, { by_ns: 90 * S })],
  [I("delay", corr, { by_ns: 120 * S })], // PASS: still received before 1500s
  [I("duplicate", alert1, { count: 1 })],
  // level 2 (children of level-1 nodes)
  [I("delay", sight2, { by_ns: 120 * S }), I("duplicate", alert1, { count: 1 })],
  [I("delay", sight2, { by_ns: 120 * S }), I("delay", { kind: "acknowledged", role: "coast_guard", nth: 1 }, { by_ns: 30 * S })],
  [I("delay", alert1, { by_ns: 60 * S }), I("drop", { kind: "weather", role: "coast_guard", nth: 1 }, {})],
  [I("drop", corr, {})], // FAIL (drop instead of delay)
  [I("duplicate", sight2, { count: 2 }), I("corrupt", { kind: "sighting", role: "harbour_ops", nth: 1 }, { field: "confidence", value: 0.65 })],
  [I("delay", corr, { by_ns: 120 * S }), I("delay", alert1, { by_ns: 60 * S })],
  [I("reorder", { kind: "sighting", role: "coast_guard", nth: 3 }, {}), I("clock_skew", { event_id: "e-0011" }, { source_id: "weather.svc", offset_ns: 45 * S })],
  [I("delay", sight2, { by_ns: 120 * S }), I("duplicate", sight2, { count: 2 }), I("delay", corr, { by_ns: 400 * S })], // FAIL (3 interventions; ddmin -> 1)
  [I("revoke", { kind: "status", role: "coast_guard" }, { role: "coast_guard", from_ns: 3000 * S }), I("delay", { kind: "weather", role: "coast_guard", nth: 1 }, { by_ns: 200 * S })], // child of root (prefix not explored)
  [I("delay", { kind: "acknowledged", role: "harbour_ops", nth: 1 }, { by_ns: 90 * S }), I("duplicate", { kind: "capacity", role: "harbour_ops", nth: 2 }, { count: 1 })],
  [I("corrupt", { kind: "sighting", role: "harbour_ops", nth: 1 }, { field: "confidence", value: 0.65 }), I("delay", { kind: "sighting", role: "harbour_ops", nth: 1 }, { by_ns: 30 * S })],
  [I("duplicate", alert1, { count: 1 }), I("delay", sight2, { by_ns: 200 * S })],
];

const runs = new Map();
const nodes = [];
function parentOf(s) {
  if (s.length === 0) return null;
  if (s.length === 1) return "root";
  const parent = s.slice(0, -1);
  const hit = schedules.find((x) => canon(x) === canon(parent)) ?? schedules.find((x) => canon(x) === canon([s[0]]));
  return hit ? `root/${schedHash(hit)}` : "root";
}
const costRng = rng("cost");
for (const s of [[], ...schedules]) {
  const { trace, verdicts } = runBranch(s);
  runs.set(trace.branch_id, { trace, verdicts });
  nodes.push({ branch_id: trace.branch_id, schedule: s, parent: parentOf(s), trace_digest: trace.digest, verdicts, cost_ns: Math.floor((38 + costRng() * 25) * 1_000_000) });
}
const failing = nodes.filter((nd) => nd.verdicts.some((v) => v.result === "FAIL")).map((nd) => nd.branch_id);
if (failing.length !== 3) throw new Error(`expected 3 failing branches, got ${failing.length}: ${failing.join(", ")}`);

const exploration = { synthetic: true, scenario_id: SCENARIO, seed: SEED, budget: 24, nodes, failing, strategy: "guided", invariants: INVARIANTS, roles: Object.keys(ROLES) };
writeFileSync(join(OUT, "exploration.json"), JSON.stringify(exploration, null, 2));

const traceFiles = ["root", ...failing];
const safe = (b) => b.replace(/\//g, "__");
for (const b of traceFiles) {
  const { trace, verdicts } = runs.get(b);
  writeFileSync(join(OUT, "traces", `${safe(b)}.json`), JSON.stringify(trace, null, 2));
  writeFileSync(join(OUT, "verdicts", `${safe(b)}.json`), JSON.stringify({ synthetic: true, branch_id: b, trace_digest: trace.digest, verdicts }, null, 2));
}

// ---- ddmin result ----
const bigFail = nodes.find((nd) => nd.schedule.length === 3 && failing.includes(nd.branch_id));
const minimalSchedule = [I("delay", corr, { by_ns: 400 * S })];
const minimalNode = nodes.find((nd) => canon(nd.schedule) === canon(minimalSchedule));
const minimal = {
  synthetic: true,
  scenario_id: SCENARIO,
  seed: SEED,
  target_invariant: "decision_matches_truth@1.1",
  failing_branch_id: bigFail.branch_id,
  failing_schedule: bigFail.schedule,
  failing_schedule_hash: schedHash(bigFail.schedule),
  minimal_schedule: minimalSchedule,
  minimal_schedule_hash: schedHash(minimalSchedule),
  minimal_branch_id: minimalNode.branch_id,
  one_minimal: true,
  steps: [
    { subset: bigFail.schedule.slice(0, 2), result: "PASS" },
    { subset: bigFail.schedule.slice(2, 3), result: "FAIL" },
    { subset: [], result: "PASS" },
  ],
  tests_run: 3,
  original_size: bigFail.schedule.length,
  minimal_size: 1,
  explanation:
    "Delaying the first correction event delivered to coast_guard by 400s pushes its received_time past the dispatch_cutter decision point (t=1500s); the actor dispatches on an alert that truth had already retracted.",
};
writeFileSync(join(OUT, "minimal.json"), JSON.stringify(minimal, null, 2));

// ---- genome ----
const minTrace = runs.get(minimalNode.branch_id).trace;
const genomeBody = {
  violated_invariant: "decision_matches_truth@1.1",
  mechanism: [
    {
      op: "delay",
      target: { kind: "correction", role: "<decider_role>", nth: 1, relative_to: "decision_point", relation: "received_after" },
      params: { by_ns: "> decision_at_ns - correction_event_time_ns - role_latency_ns" },
    },
  ],
  preconditions: [
    "has_decision_rule_depending_on(kind=correction)",
    "exists_truth_event(kind=correction) before decision_at_ns",
    "role_latency(decider_role) + delay > decision_at_ns - correction_event_time_ns",
    "decision_rule.otherwise != decision_rule.then",
  ],
  witness_shape: {
    missing_from_view: [{ kind: "correction", nth: 1 }],
    decision: { rule_kind: "exists(...) and not exists(kind='correction')", chosen: "then", truth_option: "otherwise" },
    lag_class: "information_lag_over_decision_point",
  },
};
const genome = {
  synthetic: true,
  id: sha(genomeBody),
  ...genomeBody,
  origin: { scenario_id: SCENARIO, seed: SEED, branch_id: minimalNode.branch_id, trace_digest: minTrace.digest, minimal_schedule_hash: schedHash(minimalSchedule), code_commit: "synthetic-fixture" },
  evidence_kind: "EMPIRICALLY_VALIDATED",
  claim_ceiling: "Transfer is within the shared Event v1 substrate, not across heterogeneous codebases.",
};
writeFileSync(join(OUT, "genome.json"), JSON.stringify(genome, null, 2));

// ---- challenge on convoy_lostlink ----
const cTruth = [];
let cn = 0;
const cev = (t, src, kind, payload, pri = 0) => ({
  id: `c-${String(++cn).padStart(4, "0")}`,
  scenario_id: "convoy_lostlink",
  source_id: src,
  event_time_ns: t * S,
  kind,
  payload,
  received_time_ns: null,
  geometry: null,
  unit: null,
  quality: null,
  policy_labels: ["public"],
  provenance_ids: [],
  schema_version: "event.v1",
  priority: pri,
});
cTruth.push(cev(60, "truck.2.telemetry", "heartbeat", { truck: 2, link: "ok" }));
cTruth.push(cev(240, "truck.2.telemetry", "lostlink", { truck: 2, last_fix: "A12" }, 5));
cTruth.push(cev(420, "convoy.lead", "alert", { level: "amber", reason: "truck_2_lostlink" }, 5));
cTruth.push(cev(600, "truck.2.telemetry", "correction", { corrects: "c-0002", truck: 2, link: "restored", note: "tunnel shadow, not a fault" }, 7));
const cDecision = {
  ...cev(900, "convoy.lead", "decision", { actor: "convoy.lead", rule_id: "reroute_convoy", option: "reroute", known_event_ids: ["c-0001", "c-0002", "c-0003"], at_ns: 900 * S }),
  received_time_ns: 900 * S,
};
cTruth.pop(); // decision is not a truth event
const cSchedule = [{ op: "delay", target: { kind: "correction", role: "convoy_lead", nth: 1 }, params: { by_ns: 500 * S } }];
const cViews = {
  convoy_lead: cTruth.map((e) => ({ ...e, received_time_ns: e.event_time_ns + 30 * S + (e.kind === "correction" ? 500 * S : 0) })),
  dispatcher: cTruth.map((e) => ({ ...e, received_time_ns: e.event_time_ns + 90 * S })),
};
const cTruthLog = log(cTruth);
const cViewLogs = Object.fromEntries(Object.entries(cViews).map(([r, evs]) => [r, log(evs)]));
const cDigest = sha({ truth: cTruthLog.digest, views: Object.values(cViewLogs).map((v) => v.digest).sort(), decisions: [sha(cDecision)] });
const counterexample = {
  synthetic: true,
  scenario_id: "convoy_lostlink",
  seed: 7,
  branch_id: `root/${short(cSchedule)}`,
  fault_schedule: cSchedule,
  fault_schedule_hash: short(cSchedule),
  truth: cTruthLog,
  views: cViewLogs,
  decisions: [cDecision],
  clock_end: { epoch_ns: 0, tick_ns: 1 * S, now_ns: 1800 * S, horizon_ns: 1800 * S },
  digest: cDigest,
};
const challenge = {
  synthetic: true,
  genome_id: genome.id,
  scenario_id: "convoy_lostlink",
  seed: 7,
  preconditions_met: true,
  preconditions_checked: genome.preconditions.map((p) => ({ precondition: p, met: true })),
  instantiated_schedule: cSchedule,
  verdicts: [
    { invariant_id: "decision_matches_truth", invariant_version: "1.1", result: "FAIL", witness: ["c-0004"], explanation: "reroute_convoy: chose 'reroute', truth-as-of-at_ns yields 'continue'; missing c-0004", missing_data: [], derived_at_ns: 8_120_000, trace_digest: cDigest },
    { invariant_id: "alert_acknowledged_within_300s", invariant_version: "1.0", result: "UNKNOWN", witness: [], explanation: "scenario emits no 'acknowledged' events", missing_data: ["acknowledged"], derived_at_ns: 8_120_000, trace_digest: cDigest },
    { invariant_id: "information_state_separation", invariant_version: "1.2", result: "PASS", witness: [], explanation: "every decision cites only events received by its role at at_ns", missing_data: [], derived_at_ns: 8_120_000, trace_digest: cDigest },
  ],
  reproduced: true,
  counterexample,
  claim_ceiling: "Transfer is within the shared Event v1 substrate (different domain, actors and rules), not across heterogeneous codebases.",
};
writeFileSync(join(OUT, "challenge.json"), JSON.stringify(challenge, null, 2));

// ---- evidence bundle ----
const rootTrace = runs.get("root").trace;
const ARTIFACT = sha({ artifact: "worldline-port-alert-release", version: "0.3.0-synthetic" });
const recBodies = [
  { kind: "EMPIRICALLY_VALIDATED", claim: "invariant:decision_matches_truth@1.1 FAIL reproduced on " + minimalNode.branch_id, subject: minTrace.digest, procedure: "invariants.check@1.1", result: { verdict: "FAIL", witness: ["e-0010"] }, assumptions: ["bounded horizon 3600s", "delivery jitter <= 30s"], producer: "kernel", derived_at: "2026-10-26T09:14:02Z" },
  { kind: "EMPIRICALLY_VALIDATED", claim: "invariant:* PASS on root", subject: rootTrace.digest, procedure: "invariants.check@1.1", result: { pass: 5, fail: 0, unknown: 0 }, assumptions: ["bounded horizon 3600s"], producer: "kernel", derived_at: "2026-10-26T09:14:03Z" },
  { kind: "PROVED_WITHIN_MODEL", claim: "minimal schedule is 1-minimal for decision_matches_truth@1.1", subject: minTrace.digest, procedure: "explorer.ddmin", result: { original_size: 3, minimal_size: 1, tests_run: 3 }, assumptions: ["determinism of worlds.run given (scenario, seed, schedule)", "interventions are independent at delivery time"], producer: "kernel", derived_at: "2026-10-26T09:14:05Z" },
  { kind: "EMPIRICALLY_VALIDATED", claim: "genome " + genome.id.slice(0, 19) + " reproduced on convoy_lostlink", subject: cDigest, procedure: "genome.challenge", result: { reproduced: true, preconditions_met: true }, assumptions: ["shared Event v1 substrate", "role latency convoy_lead = 30s"], producer: "agent:investigator", derived_at: "2026-10-26T09:15:40Z" },
  { kind: "ATTESTED", claim: "tests", subject: ARTIFACT, procedure: "pytest", result: { passed: 212, failed: 0, skipped: 3 }, assumptions: ["CI runner image python:3.12"], producer: "kernel", derived_at: "2026-10-26T09:16:10Z" },
  { kind: "FAILED", claim: "patch:repairer/mr-17 fixes decision_matches_truth on " + minimalNode.branch_id, subject: sha({ patch: "mr-17" }), procedure: "invariants.check@1.1", result: { verdict: "FAIL", witness: ["e-0010"], note: "patch widened window_ns but correction still arrives after at_ns" }, assumptions: ["bounded horizon 3600s"], producer: "agent:repairer", derived_at: "2026-10-26T09:31:00Z" },
  { kind: "UNKNOWN", claim: "invariant:berth_capacity_non_negative@1.0 on convoy_lostlink", subject: cDigest, procedure: "invariants.check@1.1", result: { verdict: "UNKNOWN", missing_data: ["capacity"] }, assumptions: [], producer: "kernel", derived_at: "2026-10-26T09:31:12Z" },
  { kind: "ATTESTED", claim: "artifact-built-from-commit", subject: ARTIFACT, procedure: "ci.build@gitlab", result: { commit: "synthetic-fixture", pipeline: 1042 }, assumptions: ["CI-held signing key"], producer: "kernel", derived_at: "2026-10-26T09:32:00Z" },
];
let prev = "sha256:genesis";
const records = recBodies.map((b, i) => {
  const inputs_hash = sha({ subject: b.subject, procedure: b.procedure, i });
  const body = { ...b, inputs_hash };
  const id = "sha256:" + createHash("sha256").update(prev + sha(body)).digest("hex");
  const rec = { id, ...body, prev };
  prev = id;
  return rec;
});
const bundle = {
  synthetic: true,
  version: "bundle.v1",
  records,
  checkpoint: {
    head: prev,
    count: records.length,
    sealed_at: "2026-10-26T09:33:00Z",
    signer: "ci-kernel-key-01",
    algorithm: "Ed25519",
    signature: "base64:" + createHash("sha256").update(prev + "synthetic-signature").digest("base64"),
    public_key: "base64:" + createHash("sha256").update("synthetic-public-key").digest("base64"),
  },
  verification: { ok: true, findings: [], checked_at: "2026-10-26T09:33:01Z" },
};
writeFileSync(join(OUT, "bundle.json"), JSON.stringify(bundle, null, 2));

// ---- authorization (two cases) ----
const policy = {
  required: [
    { claim_pattern: "invariant:*", min_kind: "EMPIRICALLY_VALIDATED" },
    { claim_pattern: "tests", min_kind: "ATTESTED" },
    { claim_pattern: "artifact-built-from-commit", min_kind: "ATTESTED" },
  ],
  forbid_producers_for_kinds: { ATTESTED: ["agent:*"] },
  max_age_s: 86400,
};
const authorized = {
  synthetic: true,
  decision: "AUTHORIZED",
  artifact_digest: ARTIFACT,
  policy,
  bundle_head: prev,
  evaluated_at: "2026-10-26T10:00:00Z",
  reasons: ["all required claims satisfied at or above min_kind", "bundle hash chain verified with CI public key", "no agent-produced ATTESTED records"],
  satisfied: [
    { claim_pattern: "invariant:*", record_id: records[1].id, kind: "EMPIRICALLY_VALIDATED" },
    { claim_pattern: "tests", record_id: records[4].id, kind: "ATTESTED" },
    { claim_pattern: "artifact-built-from-commit", record_id: records[7].id, kind: "ATTESTED" },
  ],
  missing: [],
  stale: [],
  downgraded: [],
};
const denied = {
  synthetic: true,
  decision: "DENIED",
  artifact_digest: ARTIFACT,
  policy,
  bundle_head: prev,
  evaluated_at: "2026-10-28T11:30:00Z",
  reasons: ["stale", "agent_report_mismatch"],
  reason_details: {
    stale: `record ${records[4].id.slice(0, 19)}... ("tests") derived_at 2026-10-26T09:16:10Z is older than max_age_s=86400 at evaluation time`,
    agent_report_mismatch: `agent:repairer reported "fixes decision_matches_truth" but kernel recomputation over trace ${minTrace.digest.slice(0, 19)}... yields FAIL with witness e-0010`,
  },
  satisfied: [
    { claim_pattern: "invariant:*", record_id: records[1].id, kind: "EMPIRICALLY_VALIDATED" },
    { claim_pattern: "artifact-built-from-commit", record_id: records[7].id, kind: "ATTESTED" },
  ],
  missing: [{ claim_pattern: "tests", min_kind: "ATTESTED", note: "only stale record available" }],
  stale: [{ record_id: records[4].id, claim: "tests", age_s: 180_830, max_age_s: 86400 }],
  downgraded: [{ record_id: records[5].id, claim: records[5].claim, from: "EMPIRICALLY_VALIDATED (agent-reported)", to: "FAILED", reason: "kernel recomputation disagrees with agent report" }],
};
writeFileSync(join(OUT, "authorization.json"), JSON.stringify({ synthetic: true, cases: { authorized, denied } }, null, 2));

// ---- index ----
const index = {
  synthetic: true,
  generated_by: "ui/scripts/gen-fixtures.mjs",
  scenario_id: SCENARIO,
  files: {
    exploration: "exploration.json",
    minimal: "minimal.json",
    genome: "genome.json",
    challenge: "challenge.json",
    bundle: "bundle.json",
    authorization: "authorization.json",
    traces: Object.fromEntries(traceFiles.map((b) => [b, `traces/${safe(b)}.json`])),
    verdicts: Object.fromEntries(traceFiles.map((b) => [b, `verdicts/${safe(b)}.json`])),
  },
};
writeFileSync(join(OUT, "index.json"), JSON.stringify(index, null, 2));
console.log("fixtures written:", { nodes: nodes.length, failing, minimal: minimalNode.branch_id, records: records.length });
