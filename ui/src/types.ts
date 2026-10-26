// Input contract: mirrors the files the pipeline writes into experiments/out (see ui/README.md "Data contract").
// The real files are authoritative; keep these types in sync with worldline/cli/main.py and docs/ARCHITECTURE.md.

export type Verdict = "PASS" | "FAIL" | "UNKNOWN";
export type EvidenceKind = "PROVED_WITHIN_MODEL" | "EMPIRICALLY_VALIDATED" | "ATTESTED" | "FAILED" | "UNKNOWN";
export type Strategy = "guided" | "random";

// ---------------------------------------------------------------- events & traces

export interface WEvent {
  id: string;
  scenario_id: string;
  source_id: string;
  event_time_ns: number;
  kind: string;
  payload: Record<string, unknown>;
  received_time_ns: number | null;
  geometry: unknown | null;
  unit: string | null;
  quality: number | null;
  policy_labels: string[];
  provenance_ids: string[];
  schema_version: string;
  priority: number;
}

export interface EventLog {
  events: WEvent[];
  digest: string;
}

export interface DecisionRule {
  id: string;
  at_ns: number;
  window_ns: number;
  when: string;
  then: string;
  otherwise: string;
}

export interface ScenarioActor {
  id: string;
  role: string;
  decision_rules: DecisionRule[];
}

export interface ScenarioRole {
  id: string;
  delivery: { base_latency_ns: number; jitter_ns: number; drop_probability: number; ordering: string };
  visibility: string[];
}

export interface ScenarioSpec {
  id: string;
  seed: number;
  clock: { epoch_ns: number; horizon_ns: number; tick_ns: number };
  roles: ScenarioRole[];
  actors: ScenarioActor[];
  invariants: string[];
  injects: unknown[];
  transitions: { id: string; kind: string; from_ns: number; to_ns: number; source_id: string; roles: string[]; params: Record<string, unknown> }[];
}

/** traces/*.json. The scenario spec is embedded; the fault schedule is NOT (look it up on the branch node / minimal / challenge). */
export interface Trace {
  scenario_id: string;
  seed: number;
  branch_id: string;
  fault_schedule_hash: string;
  truth: EventLog;
  views: Record<string, EventLog>;
  decisions: WEvent[];
  clock_end: { epoch_ns: number; tick_ns: number; now_ns: number };
  scenario: ScenarioSpec;
  digest: string;
}

// ---------------------------------------------------------------- interventions

export interface Selector {
  event_id?: string;
  kind?: string;
  role?: string;
  nth?: number;
  [k: string]: unknown;
}

export interface Intervention {
  op: "delay" | "drop" | "duplicate" | "reorder" | "corrupt" | "revoke" | "clock_skew" | string;
  target: Selector;
  params: Record<string, unknown>;
}

export type FaultSchedule = Intervention[];

/** Genome mechanisms use abstract `selector`s (kind/role/position/relation), never concrete event ids. */
export interface AbstractIntervention {
  op: string;
  selector: Record<string, unknown>;
  params: Record<string, unknown>;
}

// ---------------------------------------------------------------- verdicts & exploration

export interface VerdictRecord {
  invariant_id: string;
  invariant_version: string;
  result: Verdict;
  witness: string[];
  explanation: string;
  missing_data: string[];
  derived_at_ns: number;
  trace_digest: string;
}

export interface BranchNode {
  branch_id: string;
  schedule: FaultSchedule;
  parent: string | null;
  trace_digest: string;
  verdicts: VerdictRecord[];
  failing_invariants: string[];
  cost_ns: number;
}

/** One strategy's ExplorationResult (exploration.json → runs[strategy]). */
export interface ExplorationRun {
  scenario_id: string;
  seed: number;
  budget: number;
  schedule_size: number;
  strategy: Strategy | string;
  nodes: BranchNode[];
  failing: string[];
  n_nodes: number;
  n_failing: number;
  n_rejected: number;
  failing_rate_per_100: number;
  rejected: { schedule: FaultSchedule; reason: string }[];
  root_digest: string;
  wall_ns: number;
}

export interface ComparisonRow {
  n_nodes: number;
  n_failing: number;
  failing_rate_per_100: number;
  first_decision_fail_index: number | null;
  wall_ms: number;
}

/** exploration.json → trace_files[branch_id]. Paths are relative to the data root. */
export interface TraceRef {
  trace: string;
  verdicts: string;
  strategy?: string;
  failing_invariants?: string[];
}

/** exploration.json */
export interface ExplorationFile {
  synthetic?: boolean;
  scenario_id: string;
  scenario_path?: string;
  invariants_path?: string;
  seed: number;
  budget: number;
  schedule_size: number;
  strategies: string[];
  root_digest: string;
  runs: Record<string, ExplorationRun>;
  comparison: Record<string, ComparisonRow>;
  trace_files: Record<string, TraceRef>;
}

// ---------------------------------------------------------------- minimization

export interface CausalDiffFile {
  a: string;
  b: string;
  differs: boolean;
  truth_identical: boolean;
  decisions: { actor: string; rule_id: string; option_a: string; option_b: string; known_only_in_a: string[]; known_only_in_b: string[] }[];
  views: Record<
    string,
    {
      only_in_a: string[];
      only_in_b: string[];
      payload_changed: string[];
      received_time_moved: { event_id: string; received_a_ns: number; received_b_ns: number; delta_ns: number }[];
    }
  >;
}

/** minimal_schedule.json */
export interface MinimalFile {
  synthetic?: boolean;
  scenario_id: string;
  scenario_path?: string;
  invariants_path?: string;
  strategy: string;
  requested_invariant: string;
  target_invariant: string;
  branch_id: string;
  original: FaultSchedule;
  minimal: FaultSchedule;
  minimal_branch_id: string;
  minimal_schedule_hash: string;
  trace_digest: string;
  oracle_calls: number;
  cache_hits: number;
  one_minimal_verified: boolean;
  self_explaining: boolean;
  steps: { size: number; fails: boolean }[];
  witness: string[];
  explanation: string;
  selection_rule: string;
  candidates_considered: { branch_id: string; original_size: number; minimal_size: number; oracle_calls: number; self_explaining: boolean }[];
  candidates_skipped: unknown[];
  causal_diff: CausalDiffFile;
  trace_files: { minimal: string; minimal_verdicts: string };
}

// ---------------------------------------------------------------- genome & challenge

/** genome.json */
export interface FailureGenome {
  synthetic?: boolean;
  id: string;
  violated_invariant: string;
  mechanism: AbstractIntervention[];
  mechanism_sentence?: string;
  preconditions: string[];
  witness_shape: Record<string, unknown>;
  origin: {
    scenario_id: string;
    scenario_path?: string;
    invariants_path?: string;
    seed: number;
    branch_id: string;
    trace_digest: string;
    fault_schedule_hash?: string;
    minimal_schedule_hash: string;
    invariant_version?: string;
    code_commit: string;
    witness?: string[];
  };
  evidence_kind: EvidenceKind;
  claim_ceiling?: string;
}

export interface ChallengeResult {
  genome_id: string;
  scenario_id: string;
  scenario_path?: string;
  target_rule?: string;
  preconditions_met: boolean;
  preconditions_report: Record<string, boolean>;
  instantiated_schedule: FaultSchedule;
  verdicts: VerdictRecord[];
  reproduced: boolean;
  counterexample_digest: string | null;
  trace_file?: string;
  explanation: string;
}

/** challenge.json: the primary (positive) challenge plus negative controls (immune scenarios). */
export interface ChallengeFile {
  synthetic?: boolean;
  genome_id: string;
  genome_path?: string;
  invariants_path?: string;
  requested_seed?: number;
  seed_note?: string;
  success: boolean;
  primary: ChallengeResult;
  controls: ChallengeResult[];
  claim_ceiling?: string;
}

// ---------------------------------------------------------------- evidence

export interface EvidenceRecord {
  id: string;
  kind: EvidenceKind;
  claim: string;
  subject: string;
  procedure: string;
  inputs_hash: string;
  result: Record<string, unknown>;
  assumptions: string[];
  derived_at: string;
  producer: string;
}

/** bundle.json: records[i] is chained by links[i] = {hash, prev_hash}; the checkpoint signs the Merkle root. */
export interface Bundle {
  synthetic?: boolean;
  format: string;
  records: EvidenceRecord[];
  links: { hash: string; prev_hash: string }[];
  checkpoint?: { root: string; first_index: number; last_index: number; sealed_at: string; signer_key_id: string; signature: string };
}

export interface SatisfiedEntry {
  claim_pattern: string;
  record_id: string;
  kind: EvidenceKind;
  claim?: string;
  producer?: string;
  subject?: string;
  assumptions?: string[];
}

/** authorization.json (AUTHORIZED) and authorization_denied.json (DENIED, evaluated on bundle_tampered.json). */
export interface Authorization {
  synthetic?: boolean;
  decision: "AUTHORIZED" | "DENIED";
  artifact_digest: string;
  policy: string;
  bundle_file?: string;
  evaluated_at?: string;
  reasons: string[];
  satisfied: SatisfiedEntry[];
  missing: { claim_pattern: string; min_kind?: EvidenceKind; note?: string; [k: string]: unknown }[];
  stale: { record_id: string; claim?: string; age_s?: number; max_age_s?: number; [k: string]: unknown }[];
  downgraded: { record_id: string; claim?: string; from?: string; to?: string; reason?: string; [k: string]: unknown }[];
}

// ---------------------------------------------------------------- benchmark

export interface Aggregate {
  mean: number | null;
  median: number | null;
  n: number;
  n_null: number;
}

/** benchmark.json */
export interface Benchmark {
  synthetic?: boolean;
  scenario_id: string;
  scenario_path?: string;
  seeds: number[];
  budget: number;
  schedule_size: number;
  target_invariant: string;
  claim_ceiling: string;
  definitions: Record<string, string>;
  environment: Record<string, string>;
  exploration: {
    rows: ({ seed: number; strategy: string } & ComparisonRow)[];
    aggregate: Record<string, Record<string, Aggregate>>;
  };
  minimization: {
    cases: { seed: number; strategy: string; original_size: number; minimal_size: number; oracle_calls: number; cache_hits: number; one_minimal_verified: boolean; wall_ms: number; minimal: FaultSchedule; witness: string[] }[];
    skipped: unknown[];
    aggregate: Record<string, Aggregate | boolean>;
  };
}

// ---------------------------------------------------------------- index

/** index.json, written by `worldline report`: a manifest (path → bytes/sha256), not a logical file map. */
export interface DataIndex {
  synthetic?: boolean;
  generated_at?: string;
  code_commit?: string;
  claim_ceiling?: string;
  claims?: { id: string; claim: string; kind: EvidenceKind }[];
  digests?: Record<string, string>;
  environment?: Record<string, string>;
  files: Record<string, { bytes: number; sha256: string }>;
}

export interface LoadedData {
  index: DataIndex;
  exploration: ExplorationFile;
  minimal: MinimalFile;
  genome: FailureGenome;
  challenge: ChallengeFile;
  bundle: Bundle;
  authorization: Authorization;
  authorizationDenied: Authorization | null;
  benchmark: Benchmark | null;
}
