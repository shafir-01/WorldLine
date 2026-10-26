import type { ChallengeResult, LoadedData } from "../types";
import { challengeKey, type TraceEntry } from "../data";
import { interventionText, secs, shortBranch, shortHash, tClock } from "../format";
import { ScheduleList } from "../components/Schedule";
import { KindBadge, VerdictPill } from "../components/Badges";

interface Props {
  data: LoadedData;
  registry: Map<string, TraceEntry>;
  onOpenTimeline: (key: string) => void;
}

export default function Mechanism({ data, registry, onOpenTimeline }: Props) {
  const { minimal, genome, challenge } = data;
  const minimalKeys = new Set(minimal.minimal.map(interventionText));
  const muted = new Set(minimal.original.map((iv, i) => (minimalKeys.has(interventionText(iv)) ? -1 : i)).filter((i) => i >= 0));
  const hl = minimal.original.findIndex((iv) => minimalKeys.has(interventionText(iv)));
  const cd = minimal.causal_diff;
  const moved = Object.entries(cd?.views ?? {}).flatMap(([role, v]) => v.received_time_moved.map((m) => ({ role, ...m })));
  const branchLink = (id: string) =>
    registry.has(id) ? (
      <button className="link" onClick={() => onOpenTimeline(id)} title="Open in Timeline">
        {shortBranch(id)}
      </button>
    ) : (
      <code title="no trace exported">{shortBranch(id)}</code>
    );

  return (
    <div className="view">
      <header className="view-head">
        <div>
          <h1>Mechanism</h1>
          <p className="lede">
            From a failing world to a portable mechanism: <b>ddmin</b> strips the fault schedule to what is necessary and verifies 1-minimality, <b>genome.extract</b> abstracts concrete event ids into selectors and preconditions, and{" "}
            <b>genome.challenge</b> instantiates the mechanism on other scenarios: one that should break (primary) and one built to be immune (negative control).
          </p>
        </div>
      </header>

      <div className="cards">
        {/* ddmin */}
        <section className="card">
          <div className="card-head">
            <h2>Minimal schedule (ddmin)</h2>
            <span className="chip">{minimal.target_invariant}</span>
          </div>
          <p className="headline">
            <b>
              {minimal.minimal.length} of {minimal.original.length} interventions
            </b>{" "}
            explain{minimal.minimal.length === 1 ? "s" : ""} the failure.
          </p>
          <div className="flags">
            <Flag ok={minimal.one_minimal_verified} label="one_minimal_verified" help="Independent check: the minimal schedule fails and every single-intervention removal passes." />
            <Flag ok={minimal.self_explaining} label="self_explaining" help="The minimal intervention targets an event that appears in the FAIL witness." />
            <span className="chip" title="Distinct schedules ddmin executed (cache hits excluded)">
              oracle calls {minimal.oracle_calls}
            </span>
            <span className="chip" title="Schedules answered from the cache">
              cache hits {minimal.cache_hits}
            </span>
          </div>
          <h4>Original failing schedule</h4>
          <ScheduleList schedule={minimal.original} highlightIndex={hl} mutedIndices={muted} />
          <div className="muted small">
            branch {branchLink(minimal.branch_id)} ({minimal.strategy} run) · dimmed interventions were removed without changing the verdict
          </div>
          <h4>Minimal schedule</h4>
          <ScheduleList schedule={minimal.minimal} highlightIndex={0} />
          <div className="muted small">
            reproduces as branch {branchLink(minimal.minimal_branch_id)} · schedule hash <code title={minimal.minimal_schedule_hash}>{shortHash(minimal.minimal_schedule_hash, 14)}</code> · trace{" "}
            <code title={minimal.trace_digest}>{shortHash(minimal.trace_digest, 14)}</code>
          </div>
          <p className="small">{minimal.explanation}</p>
          <div className="small">
            witness:{" "}
            {minimal.witness.map((w) => (
              <code key={w} className="wit">
                {w}
              </code>
            ))}
          </div>

          <h4>Reduction steps ({minimal.steps.length})</h4>
          <ol className="steps">
            {minimal.steps.map((s, i) => (
              <li key={i}>
                <VerdictPill v={s.fails ? "FAIL" : "PASS"} />
                <span className="small">
                  subset of size {s.size} {s.fails ? "still fails" : "passes"}
                </span>
              </li>
            ))}
          </ol>

          {cd && (
            <>
              <h4>
                Causal diff <span className="muted">{shortBranch(cd.a)} → {shortBranch(cd.b)}</span>
              </h4>
              <div className="small muted">
                truth {cd.truth_identical ? "identical" : "differs"} · views {cd.differs ? "differ" : "identical"}
              </div>
              <ul className="plain">
                {cd.decisions.map((d) => (
                  <li key={d.rule_id} className="small">
                    <b>{d.actor}</b> · {d.rule_id}: <code>{d.option_a}</code> → <code className="bad">{d.option_b}</code>
                    {d.known_only_in_a.length > 0 && <div className="muted">known only in root: {d.known_only_in_a.join(", ")}</div>}
                    {d.known_only_in_b.length > 0 && <div className="muted">known only in branch: {d.known_only_in_b.join(", ")}</div>}
                  </li>
                ))}
                {moved.map((m) => (
                  <li key={`${m.role}/${m.event_id}`} className="small">
                    <code>{m.event_id}</code> in <b>{m.role}</b>: received {tClock(m.received_a_ns)} → {tClock(m.received_b_ns)} <span className="bad">(+{secs(m.delta_ns)})</span>
                  </li>
                ))}
              </ul>
            </>
          )}

          <details className="details">
            <summary className="small">
              Selection rule and {minimal.candidates_considered.length} candidates considered
            </summary>
            <p className="small muted">{minimal.selection_rule}</p>
            <table className="table small">
              <thead>
                <tr>
                  <th>branch</th>
                  <th className="num">orig</th>
                  <th className="num">min</th>
                  <th className="num">oracle</th>
                  <th>self-explaining</th>
                </tr>
              </thead>
              <tbody>
                {minimal.candidates_considered.map((c) => (
                  <tr key={c.branch_id} className={c.branch_id === minimal.branch_id ? "active" : ""}>
                    <td>{branchLink(c.branch_id)}</td>
                    <td className="num">{c.original_size}</td>
                    <td className="num">{c.minimal_size}</td>
                    <td className="num">{c.oracle_calls}</td>
                    <td>{c.self_explaining ? "yes" : "no"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </section>

        {/* genome */}
        <section className="card">
          <div className="card-head">
            <h2>Failure genome</h2>
            <KindBadge kind={genome.evidence_kind} />
          </div>
          <code className="block" title={genome.id}>
            {genome.id}
          </code>
          {genome.mechanism_sentence && <p className="headline sentence">{genome.mechanism_sentence}</p>}
          <dl className="kv">
            <dt>violates</dt>
            <dd>
              <code>{genome.violated_invariant}</code>
              {genome.origin.invariant_version && <span className="muted">@{genome.origin.invariant_version}</span>}
            </dd>
            <dt>origin</dt>
            <dd>
              {genome.origin.scenario_id} · seed {genome.origin.seed} · {branchLink(genome.origin.branch_id)}
              <div className="muted small">
                trace {shortHash(genome.origin.trace_digest, 14)} · schedule {shortHash(genome.origin.minimal_schedule_hash, 14)} · commit {genome.origin.code_commit.slice(0, 10)}
              </div>
              {genome.origin.witness && (
                <div className="small">
                  witness:{" "}
                  {genome.origin.witness.map((w) => (
                    <code key={w} className="wit">
                      {w}
                    </code>
                  ))}
                </div>
              )}
            </dd>
          </dl>
          <h4>Mechanism (abstract selectors only)</h4>
          <ul className="schedule">
            {genome.mechanism.map((iv, i) => (
              <li key={i} className="iv iv-hl">
                <span className={`op op-${iv.op}`}>{iv.op}</span>
                <span className="sel">
                  {Object.entries(iv.selector)
                    .map(([k, v]) => `${k}=${String(v)}`)
                    .join(" ")}
                </span>
                <span className="params">
                  {Object.entries(iv.params)
                    .map(([k, v]) => (typeof v === "number" && k.endsWith("_ns") ? `${k.replace(/_ns$/, "")}=${secs(v)}` : `${k}=${String(v)}`))
                    .join(", ")}
                </span>
              </li>
            ))}
          </ul>
          <h4>Preconditions</h4>
          <ul className="pre">
            {genome.preconditions.map((p) => (
              <li key={p}>
                <code>{p}</code>
              </li>
            ))}
          </ul>
          <h4>Witness shape</h4>
          <pre className="json">{JSON.stringify(genome.witness_shape, null, 2)}</pre>
          <p className="ceiling">{genome.claim_ceiling ?? "Transfer is within the shared Event v1 substrate, not across heterogeneous codebases."}</p>
        </section>

        {/* challenge */}
        <section className="card">
          <div className="card-head">
            <h2>Challenge</h2>
            <span className={`pill ${challenge.success ? "pill-pass" : "pill-fail"}`}>{challenge.success ? "SUCCESS" : "FAILED"}</span>
          </div>
          <p className="small">
            Genome <code>{shortHash(challenge.genome_id.replace(/^genome:/, ""), 12)}</code> instantiated on other scenarios. Success means the primary reproduced <b>and</b> every negative control did not.
            {challenge.seed_note && (
              <span className="muted" title={challenge.seed_note}>
                {" "}
                (deterministic in each scenario's own seed)
              </span>
            )}
          </p>
          <ChallengeCard r={challenge.primary} kind="primary" traceKey={registry.has(challengeKey(challenge.primary.scenario_id)) ? challengeKey(challenge.primary.scenario_id) : null} onOpenTimeline={onOpenTimeline} />
          {challenge.controls.map((c) => (
            <ChallengeCard key={c.scenario_id} r={c} kind="control" traceKey={registry.has(challengeKey(c.scenario_id)) ? challengeKey(c.scenario_id) : null} onOpenTimeline={onOpenTimeline} />
          ))}
          <p className="ceiling">{challenge.claim_ceiling ?? "Transfer is within the shared Event v1 substrate, not across heterogeneous codebases."}</p>
        </section>
      </div>
    </div>
  );
}

function Flag({ ok, label, help }: { ok: boolean; label: string; help: string }) {
  return (
    <span className={`flag-pill ${ok ? "ok" : "nok"}`} title={help}>
      <span className="tick">{ok ? "✓" : "✗"}</span> {label}
    </span>
  );
}

function ChallengeCard({ r, kind, traceKey, onOpenTimeline }: { r: ChallengeResult; kind: "primary" | "control"; traceKey: string | null; onOpenTimeline: (k: string) => void }) {
  // For a negative control the desired outcome is NOT reproduced (with preconditions met): the mechanism is specific, not a universal failure.
  const expected = kind === "primary" ? r.reproduced : r.preconditions_met && !r.reproduced;
  const outcome = kind === "primary" ? (r.reproduced ? "REPRODUCED" : "NOT REPRODUCED") : !r.preconditions_met ? "NOT APPLICABLE" : r.reproduced ? "REPRODUCED (unexpected)" : "NOT REPRODUCED";
  const checks = Object.entries(r.preconditions_report);
  const ceDecision = r.verdicts.find((v) => v.result === "FAIL");
  return (
    <div className={`challenge ${kind} ${expected ? "expected" : "unexpected"}`}>
      <div className="card-head">
        <div>
          <span className="eyebrow">{kind === "primary" ? "primary" : "negative control"}</span>
          <h3 className="ch-title">
            <code>{r.scenario_id}</code>
            {r.target_rule && <span className="muted small"> · rule {r.target_rule}</span>}
          </h3>
        </div>
        <span className={`pill ${expected ? "pill-pass" : "pill-fail"}`} title={kind === "control" ? "A control passes when preconditions are met and the mechanism does NOT reproduce" : ""}>
          {outcome}
        </span>
      </div>
      {kind === "control" && expected && <div className="control-note small">Preconditions met, mechanism instantiated, not reproduced: the control held, so the genome is specific rather than a trivial "any delay breaks everything".</div>}
      <details className="details" open={kind === "primary"}>
        <summary className="small">
          preconditions {checks.filter(([, v]) => v).length}/{checks.length} met · schedule · verdicts
        </summary>
        <ul className="pre">
          {checks.map(([p, met]) => (
            <li key={p} className={met ? "ok" : "nok"}>
              <span className="tick">{met ? "✓" : "✗"}</span> <code>{p}</code>
            </li>
          ))}
        </ul>
        <h4>Instantiated schedule</h4>
        <ScheduleList schedule={r.instantiated_schedule} highlightIndex={0} />
        <h4>Verdicts</h4>
        <ul className="verdicts compact">
          {r.verdicts.map((v) => (
            <li key={v.invariant_id} className={`verdict verdict-${v.result.toLowerCase()}`}>
              <div className="verdict-head">
                <VerdictPill v={v.result} /> <span className="inv">{v.invariant_id}</span>
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
      </details>
      <p className="small">{r.explanation}</p>
      {r.counterexample_digest && (
        <div className="small muted">
          counterexample trace <code title={r.counterexample_digest}>{shortHash(r.counterexample_digest, 14)}</code>
          {ceDecision && <> · {ceDecision.witness.length} witness events</>}
        </div>
      )}
      {traceKey && (
        <button className="btn btn-sm" onClick={() => onOpenTimeline(traceKey)}>
          Open counterexample timeline →
        </button>
      )}
    </div>
  );
}
