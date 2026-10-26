// Input contract: mirrors docs/ARCHITECTURE.md data shapes. Keep in sync with the Python dataclasses.

export type Verdict = "PASS" | "FAIL" | "UNKNOWN";
export type EvidenceKind = "PROVED_WITHIN_MODEL" | "EMPIRICALLY_VALIDATED" | "ATTESTED" | "FAILED" | "UNKNOWN";

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

export interface DecisionPayload {
  actor: string;
  rule_id: string;
  option: string;
  known_event_ids: string[];
  at_ns: number;
}

export interface DecisionRule {
  id: string;
  actor: string;
  role: string;
  at_ns: number;
  window_ns: number;
  when: string;
  then: string;
  otherwise: string;
}

export interface Trace {
  synthetic?: boolean;
  scenario_id: string;
  seed: number;
  branch_id: string;
  fault_schedule?: Intervention[];
  fault_schedule_hash: string;
  truth: EventLog;
  views: Record<string, EventLog>;
  decisions: WEvent[];
  clock_end: { epoch_ns: number; tick_ns: number; now_ns: number; horizon_ns?: number };
  roles?: Record<string, { base_latency_ns: number; jitter_ns: number; drop_probability: number; ordering: string; visibility?: string[] }>;
  decision_rules?: DecisionRule[];
  digest: string;
}

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
  cost_ns: number;
}

export interface InvariantMeta {
  id: string;
  version: string;
  kind: string;
  description?: string;
}

export interface ExplorationResult {
  synthetic?: boolean;
  scenario_id: string;
  seed: number;
  budget: number;
  nodes: BranchNode[];
  failing: string[];
  strategy: "random" | "guided" | string;
  invariants?: InvariantMeta[];
  roles?: string[];
}

export interface MinimalResult {
  synthetic?: boolean;
  scenario_id: string;
  seed: number;
  target_invariant: string;
  failing_branch_id: string;
  failing_schedule: FaultSchedule;
  failing_schedule_hash: string;
  minimal_schedule: FaultSchedule;
  minimal_schedule_hash: string;
  minimal_branch_id?: string;
  one_minimal: boolean;
  steps?: { subset: FaultSchedule; result: Verdict }[];
  tests_run?: number;
  original_size: number;
  minimal_size: number;
  explanation?: string;
}

export interface FailureGenome {
  synthetic?: boolean;
  id: string;
  violated_invariant: string;
  mechanism: Intervention[];
  preconditions: string[];
  witness_shape: Record<string, unknown>;
  origin: { scenario_id: string; seed: number; branch_id: string; trace_digest: string; minimal_schedule_hash: string; code_commit: string };
  evidence_kind: EvidenceKind;
  claim_ceiling?: string;
}

export interface ChallengeResult {
  synthetic?: boolean;
  genome_id: string;
  scenario_id: string;
  seed?: number;
  preconditions_met: boolean;
  preconditions_checked?: { precondition: string; met: boolean }[];
  instantiated_schedule: FaultSchedule;
  verdicts: VerdictRecord[];
  reproduced: boolean;
  counterexample: Trace | null;
  claim_ceiling?: string;
}

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
  prev?: string;
}

export interface Bundle {
  synthetic?: boolean;
  version?: string;
  records: EvidenceRecord[];
  checkpoint?: { head: string; count: number; sealed_at: string; signer: string; algorithm: string; signature: string; public_key: string };
  verification?: { ok: boolean; findings: string[]; checked_at: string };
}

export interface Policy {
  required: { claim_pattern: string; min_kind: EvidenceKind }[];
  forbid_producers_for_kinds: Record<string, string[]>;
  max_age_s: number;
}

export interface Authorization {
  synthetic?: boolean;
  decision: "AUTHORIZED" | "DENIED";
  artifact_digest: string;
  policy?: Policy;
  bundle_head?: string;
  evaluated_at?: string;
  reasons: string[];
  reason_details?: Record<string, string>;
  satisfied: { claim_pattern: string; record_id: string; kind: EvidenceKind }[];
  missing: { claim_pattern: string; min_kind: EvidenceKind; note?: string }[];
  stale: { record_id: string; claim: string; age_s: number; max_age_s: number }[];
  downgraded: { record_id: string; claim: string; from: string; to: string; reason: string }[];
}

export interface AuthorizationFile {
  synthetic?: boolean;
  cases: { authorized: Authorization; denied: Authorization };
}

export interface DataIndex {
  synthetic?: boolean;
  generated_by?: string;
  scenario_id?: string;
  files: {
    exploration: string;
    minimal: string;
    genome: string;
    challenge: string;
    bundle: string;
    authorization: string;
    traces: Record<string, string>;
    verdicts: Record<string, string>;
  };
}

export interface LoadedData {
  index: DataIndex;
  exploration: ExplorationResult;
  minimal: MinimalResult;
  genome: FailureGenome;
  challenge: ChallengeResult;
  bundle: Bundle;
  authorization: AuthorizationFile;
}
