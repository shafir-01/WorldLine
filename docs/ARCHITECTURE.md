# WORLDLINE architecture and module contracts

WORLDLINE answers one question about a software system: **what would it do if the world were different, and which differences actually matter?** It answers with a reproducible minimal counterexample, converts that counterexample into a portable failure mechanism that can challenge a second system, types every piece of evidence by certainty, and lets nothing deploy on evidence it cannot type.

This document is the binding contract between modules. Agents implementing a module must keep these signatures and data shapes. Internal design inside a module is free.

## Pipeline

```
scenario.yaml ──► worlds.run ──► Trace ──► invariants.check ──► VerdictRecord[]
                     ▲                                 │
     interventions   │  explorer.explore (branch tree) │  FAIL
     (FaultSchedule) └───────── explorer.ddmin ◄───────┘
                                     │ minimal FaultSchedule
                                     ▼
                              genome.extract ──► FailureGenome (abstract, schema-level)
                                     │
                     scenario_B.yaml ─┴─► genome.challenge ──► ChallengeResult (+ counterexample)
                                     │
                              evidence.record ──► EvidenceRecord[] ──► Bundle (hash chain, signed)
                                     │
                       evidence.kernel.authorize(bundle, policy, artifact_digest) ──► Authorization
```

Everything downstream of `scenario.yaml` is deterministic given `(scenario, seed, fault_schedule)`. Same inputs must yield byte-identical `Trace.digest`.

## Shared core (`worldline.core`, done)

- `Event` (frozen dataclass): `id, scenario_id, source_id, event_time_ns, kind, payload, received_time_ns, geometry, unit, quality, policy_labels, provenance_ids, schema_version, priority`. `event.content_hash`, `event.sort_key()` = `(event_time_ns, -priority, id)`, `event.replace(**changes)`, `event.with_receipt(t)`.
- `EventLog`: append-only, hash-chained. `append, extend, ordered(), digest, verify() -> [bad indices], to_dict/from_dict`.
- `VirtualClock(epoch_ns, tick_ns, now_ns)`: `advance, advance_to, snapshot, restore`. Never reads wall time.
- `DeterministicRNG(seed, path)`: `fork(label)` gives independent sub-streams. All randomness in the whole system goes through this.
- `Verdict = PASS | FAIL | UNKNOWN`. `EvidenceKind = PROVED_WITHIN_MODEL | EMPIRICALLY_VALIDATED | ATTESTED | FAILED | UNKNOWN`.
- `canonical_json(obj)`, `content_hash(obj)`.

Time rule: `event_time_ns` is simulated truth time. `received_time_ns` is when a specific role got it. They are never merged. A role view only contains events with `received_time_ns <= query_time`.

## `worldline.worlds` — scenario grammar, deterministic runner, snapshots and branches

```python
@dataclass
class Scenario:            # loaded from YAML, see scenarios/port_alert.yaml
    id: str
    seed: int
    clock: dict            # {epoch_ns, tick_ns, horizon_ns}
    roles: list[Role]      # Role(id, delivery: DeliveryPolicy, visibility: list[str] policy_labels allowed)
    actors: list[Actor]    # Actor(id, role, decision_rules: list[DecisionRule])
    injects: list[Inject]  # Inject(at_ns, source_id, kind, payload, policy_labels, geometry?) -> truth events
    transitions: list[TruthTransition]  # state changes in the simulated world (e.g. "outage" from t1 to t2 kills source X)
    invariants: list[str]  # ids of invariants to check by default (defined in invariants/*.yaml)

@dataclass
class DeliveryPolicy:      # per role: how truth becomes observation
    base_latency_ns: int
    jitter_ns: int         # drawn from DeterministicRNG.fork(f"deliver/{role}/{event_id}")
    drop_probability: float
    ordering: str          # "fifo" | "network" (jitter may reorder)

@dataclass
class DecisionRule:        # deterministic policy, no LLM
    id: str
    at_ns: int                      # decision point
    when: str                       # simple predicate DSL over known events, e.g. "count(kind='sighting', payload.confidence>=0.6) >= 2"
    then: str                       # option chosen if predicate true
    otherwise: str
    window_ns: int                  # only events received within [at_ns-window_ns, at_ns] count

@dataclass
class Trace:
    scenario_id: str
    seed: int
    branch_id: str                  # "root" or "root/<schedule_hash>"
    fault_schedule_hash: str
    truth: EventLog                 # private simulation truth
    views: dict[str, EventLog]      # role_id -> events as received (received_time_ns set), subset of truth + duplicates/corruptions
    decisions: list[Event]          # kind="decision", payload={actor, rule_id, option, known_event_ids: [...], at_ns}
    clock_end: dict
    digest: str                     # content_hash over (truth.digest, sorted view digests, decision hashes)

def load_scenario(path) -> Scenario
def run(scenario: Scenario, schedule: "FaultSchedule" = (), *, snapshot: "Snapshot|None" = None) -> Trace
def snapshot_at(trace: Trace, t_ns: int) -> Snapshot      # everything up to t_ns, frozen
def branch(snapshot: Snapshot, schedule: FaultSchedule) -> Trace  # replay to t_ns identically, then diverge
def view_as_known_at(trace, role, t_ns) -> list[Event]   # AS_KNOWN_AT: received_time_ns <= t_ns
def view_as_observed_at(trace, role, t_ns) -> list[Event] # AS_OBSERVED_AT: event_time_ns <= t_ns AND received
```

Separation rule enforced in code and tests: nothing in `views` or `decisions` may read `truth` directly. Decision rules evaluate only over `view_as_known_at(trace, actor.role, at_ns)`. A test must prove a withheld truth event never appears in a view.

## `worldline.interventions` — operators and mutators

```python
@dataclass(frozen=True)
class Intervention:
    op: str          # "delay" | "drop" | "duplicate" | "reorder" | "corrupt" | "revoke" | "clock_skew"
    target: Selector # concrete: {"event_id": "..."} or abstract: {"kind": "sighting", "role": "harbour_ops", "nth": 1}
    params: dict     # delay: {by_ns}; duplicate: {count}; corrupt: {field, value}; revoke: {role, from_ns}; clock_skew: {source_id, offset_ns}
    def hash(self) -> str

FaultSchedule = tuple[Intervention, ...]
def schedule_hash(s: FaultSchedule) -> str
def apply(schedule, truth_events, views_in_progress, rng) -> ...   # hooked into worlds.run at delivery time; worlds calls interventions, not the reverse
def resolve(selector, trace_or_scenario) -> list[str]  # abstract selector -> concrete event ids (deterministic order)

class Mutator:  # generates candidate interventions from a baseline trace
    def propose(self, trace: Trace, rng: DeterministicRNG, budget: int) -> list[Intervention]
# Families: DelayMutator, DropMutator, DuplicateMutator, ReorderMutator, CorruptMutator, RevokeMutator, ClockSkewMutator
```

`worlds.run` applies the schedule at delivery time: each truth event, for each role, passes through `interventions.apply_at_delivery(...)` which may change `received_time_ns`, suppress delivery, duplicate, or alter payload (corruption marks `provenance_ids` with the original and sets `quality`).

## `worldline.invariants` — three-valued checker with witnesses

Invariants are declared in YAML (`invariants/*.yaml`) and loaded into:

```python
@dataclass
class Invariant:
    id: str
    version: str
    kind: str            # "always" | "response" | "absence" | "epistemic" | "decision"
    description: str
    params: dict
    required_signals: list[str]  # event kinds that must exist for a verdict; if absent -> UNKNOWN

@dataclass
class VerdictRecord:
    invariant_id: str
    invariant_version: str
    result: Verdict
    witness: list[str]           # event ids forming the counterexample (minimal segment for FAIL)
    explanation: str
    missing_data: list[str]      # why UNKNOWN
    derived_at_ns: int           # evaluator clock, separate from event time
    trace_digest: str

def load_invariants(path) -> dict[str, Invariant]
def check(trace: Trace, inv: Invariant) -> VerdictRecord
def check_all(trace, invariants) -> list[VerdictRecord]
```

Required kinds, all with FAIL witnesses and UNKNOWN on missing signals:

- `response`: `G(A -> F<=N B)` within a role's view (e.g. every `alert` is `acknowledged` within N).
- `absence`: no event matching P in a role's view (e.g. role `coast_guard` never sees `policy_labels` containing `harbour_private`).
- `epistemic`: no event appears in any view before its `received_time_ns`; no decision's `known_event_ids` includes an event not received by that actor's role at `at_ns`. (This is the "information-state separation" invariant.)
- `decision`: a decision rule's chosen option must equal the option the rule would produce over **truth as of at_ns** (i.e. the decision was wrong because of what the actor did not yet know). FAIL witness = the truth events missing from the actor's view at decision time. This is the invariant that makes "stale information caused a wrong decision" a first-class detectable failure.
- `always`: state predicate over truth (e.g. no negative capacity).

## `worldline.explorer` — branching exploration, minimization, causal diff

```python
@dataclass
class BranchNode:
    branch_id: str; schedule: FaultSchedule; parent: str|None; trace_digest: str
    verdicts: list[VerdictRecord]; cost_ns: int   # wall time, measured

@dataclass
class ExplorationResult:
    scenario_id: str; seed: int; budget: int
    nodes: list[BranchNode]; failing: list[str]   # branch ids with any FAIL
    strategy: str                                  # "random" | "guided"
    def to_dict(self)

def explore(scenario, invariants, *, budget: int, seed: int, strategy="guided", mutators=None) -> ExplorationResult
def ddmin(scenario, invariants, failing_schedule: FaultSchedule, target_invariant: str) -> FaultSchedule   # Zeller ddmin, 1-minimal
def causal_diff(trace_a: Trace, trace_b: Trace) -> dict  # events only in a, only in b, received-time deltas, decisions that differ
```

`strategy="random"` is the baseline (uniform random schedules of size k). `strategy="guided"` prioritises interventions touching events that decision rules depend on (read from the Scenario, not from truth). The experiment must report failures-found-per-budget for both.

## `worldline.genome` — portable failure mechanism

```python
@dataclass
class FailureGenome:
    id: str                               # content hash
    violated_invariant: str               # invariant id + version
    mechanism: list[Intervention]         # ABSTRACT selectors only (kind/role/nth/relative timing), never event ids
    preconditions: list[str]              # structural features required: "has_decision_rule_depending_on(kind=sighting)", "role_latency(role) < delay"
    witness_shape: dict                   # abstract description of the counterexample
    origin: dict                          # scenario_id, seed, branch_id, trace_digest, minimal_schedule_hash, code_commit
    evidence_kind: EvidenceKind           # EMPIRICALLY_VALIDATED on origin

def extract(trace, minimal_schedule, verdict, scenario) -> FailureGenome   # abstracts concrete ids to selectors
def challenge(genome, scenario_b, invariants, *, seed) -> ChallengeResult
# ChallengeResult: {genome_id, scenario_id, preconditions_met: bool, instantiated_schedule, verdicts, reproduced: bool, counterexample: Trace|None}
```

Honest framing: transfer is **within the shared Event v1 substrate** across scenarios with different domains, actors, and decision rules. It is not transfer across heterogeneous codebases. Say so everywhere.

## `worldline.evidence` — typed evidence, sealed bundles, authorization kernel

```python
@dataclass
class EvidenceRecord:
    id: str; kind: EvidenceKind; claim: str
    subject: str            # artifact digest or trace digest the claim is about
    procedure: str          # e.g. "invariants.check@<version>", "pytest", "explorer.ddmin"
    inputs_hash: str; result: dict
    assumptions: list[str]  # e.g. "bounded horizon 3600s", "delivery jitter <= 30s"
    derived_at: str         # ISO UTC wall time (this is the one place wall time is allowed; it is metadata)
    producer: str           # "kernel" | "agent:<name>" ; kernel is the only producer allowed to emit ATTESTED

class Bundle:   # hash chain of EvidenceRecords + signed checkpoint (Ed25519 via `cryptography`)
    def add(rec); def seal(signing_key) -> Checkpoint; def verify(public_key) -> VerificationResult  # distinct findings for edit/drop/reorder/wrong key/truncation
    def to_json / from_json

@dataclass
class Policy:   # what a release needs
    required: list[dict]  # [{"claim_pattern": "invariant:*", "min_kind": "EMPIRICALLY_VALIDATED"}, {"claim_pattern":"tests", ...}]
    forbid_producers_for_kinds: dict  # {"ATTESTED": ["agent:*"]}
    max_age_s: int

def authorize(bundle, policy, artifact_digest, public_key, now) -> Authorization
# Authorization: {decision: "AUTHORIZED"|"DENIED", artifact_digest, reasons: [...], satisfied: [...], missing: [...], stale: [...], downgraded: [...]}
def revoke_if_assumption_invalid(bundle, invalidated_assumption) -> Authorization
```

Kernel independence: the kernel CLI (`worldline verify`, `worldline authorize`) reads only the bundle, the policy, and the public key. It never trusts a `result` field it did not recompute when recomputation is possible (invariant verdicts are recomputed from the trace). Agents produce traces, patches, and candidate evidence; the kernel decides.

## `worldline.cli`

Typer app `worldline` with: `run`, `explore`, `minimize`, `genome extract|challenge`, `verify`, `authorize`, `report` (writes JSON for the UI into `experiments/out/`). Every command prints the digests it produced.

## `ui/`

Vite + React + TypeScript static viewer over `experiments/out/*.json`: branch tree, per-role timelines (event vs received time), decision points, FAIL witness highlighting, genome card, evidence chain with certainty badges, authorization verdict. No backend. Must render from committed fixture JSON so it works on GitLab Pages.

## GitLab integration

- `.gitlab-ci.yml`: `test` → `explore` (artifacts: experiments/out) → `kernel-verify` (recomputes verdicts, seals bundle with CI-held key) → `authorize` (gate) → `pages`.
- Duo Agent Platform: `AGENTS.md`, `.gitlab/duo/chat-rules.md`, `.gitlab/duo/agent-config.yml`, and custom agent definitions in `docs/duo/` (Investigator, Repairer, Skeptic). The Repairer may edit scenarios/code; it cannot edit `invariants/`, `worldline/evidence/`, or the CI kernel jobs (CODEOWNERS + protected paths).

## Non-negotiables

- No wall-clock time in simulation. No LLM in the decision path of the simulated world. No fabricated numbers: `experiments/out/benchmark.json` is written by code that ran.
- Every FAIL has a witness list of event ids. Every UNKNOWN names the missing signal.
- Every public claim carries its `EvidenceKind` and its claim ceiling.
