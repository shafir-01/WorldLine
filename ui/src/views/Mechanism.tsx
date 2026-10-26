import type { ChallengeResult, FailureGenome, MinimalResult } from "../types";
import { interventionText, secs, shortBranch, shortHash, tClock } from "../format";
import { ScheduleList } from "../components/Schedule";
import { KindBadge, VerdictPill } from "../components/Badges";

interface Props {
  minimal: MinimalResult;
  genome: FailureGenome;
  challenge: ChallengeResult;
  onSelectBranch: (id: string) => void;
}

export default function Mechanism({ minimal, genome, challenge, onSelectBranch }: Props) {
  const minimalKeys = new Set(minimal.minimal_schedule.map(interventionText));
  const muted = new Set(minimal.failing_schedule.map((iv, i) => (minimalKeys.has(interventionText(iv)) ? -1 : i)).filter((i) => i >= 0));
  const hl = minimal.failing_schedule.findIndex((iv) => minimalKeys.has(interventionText(iv)));
  const ce = challenge.counterexample;
  const ceDecision = ce?.decisions[0];
  const ceWitness = new Set(challenge.verdicts.filter((v) => v.result === "FAIL").flatMap((v) => v.witness));

  return (
    <div className="view">
      <header className="view-head">
        <div>
          <h1>Mechanism</h1>
          <p className="lede">
            From a failing world to a portable mechanism: <b>ddmin</b> strips the fault schedule to what is necessary, <b>genome.extract</b> abstracts concrete event ids into selectors and preconditions, and{" "}
            <b>genome.challenge</b> tries the mechanism on a second scenario.
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
              {minimal.minimal_size} intervention{minimal.minimal_size === 1 ? "" : "s"} out of {minimal.original_size}
            </b>{" "}
            explain{minimal.minimal_size === 1 ? "s" : ""} the failure{minimal.one_minimal ? " (1-minimal)" : ""}.
          </p>
          <h4>Failing schedule</h4>
          <ScheduleList schedule={minimal.failing_schedule} highlightIndex={hl} mutedIndices={muted} />
          <div className="muted small">
            branch{" "}
            <button className="link" onClick={() => onSelectBranch(minimal.failing_branch_id)}>
              {shortBranch(minimal.failing_branch_id)}
            </button>{" "}
            · dimmed interventions were removed without changing the verdict
          </div>
          <h4>Minimal schedule</h4>
          <ScheduleList schedule={minimal.minimal_schedule} highlightIndex={0} />
          {minimal.minimal_branch_id && (
            <div className="muted small">
              reproduces as branch{" "}
              <button className="link" onClick={() => onSelectBranch(minimal.minimal_branch_id!)}>
                {shortBranch(minimal.minimal_branch_id)}
              </button>{" "}
              · schedule hash {minimal.minimal_schedule_hash}
            </div>
          )}
          {minimal.explanation && <p className="small">{minimal.explanation}</p>}
          {minimal.steps && (
            <>
              <h4>Reduction steps ({minimal.tests_run ?? minimal.steps.length} tests)</h4>
              <ol className="steps">
                {minimal.steps.map((s, i) => (
                  <li key={i}>
                    <VerdictPill v={s.result} />
                    <span className="small">{s.subset.length ? s.subset.map(interventionText).join(" + ") : "(empty)"}</span>
                  </li>
                ))}
              </ol>
            </>
          )}
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
          <dl className="kv">
            <dt>violates</dt>
            <dd>
              <code>{genome.violated_invariant}</code>
            </dd>
            <dt>origin</dt>
            <dd>
              {genome.origin.scenario_id} · seed {genome.origin.seed} ·{" "}
              <button className="link" onClick={() => onSelectBranch(genome.origin.branch_id)}>
                {shortBranch(genome.origin.branch_id)}
              </button>
              <div className="muted small">
                trace {shortHash(genome.origin.trace_digest, 14)} · schedule {genome.origin.minimal_schedule_hash} · commit {genome.origin.code_commit}
              </div>
            </dd>
          </dl>
          <h4>Mechanism (abstract selectors only)</h4>
          <ul className="schedule">
            {genome.mechanism.map((iv, i) => (
              <li key={i} className="iv iv-hl">
                <span className={`op op-${iv.op}`}>{iv.op}</span>
                <span className="sel">
                  {Object.entries(iv.target)
                    .map(([k, v]) => `${k}=${String(v)}`)
                    .join(" ")}
                </span>
                <span className="params">
                  {Object.entries(iv.params)
                    .map(([k, v]) => `${k}: ${String(v)}`)
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
            <h2>
              Challenge: <code>{challenge.scenario_id}</code>
            </h2>
            <span className={`pill ${challenge.reproduced ? "pill-fail" : "pill-pass"}`}>{challenge.reproduced ? "REPRODUCED" : "NOT REPRODUCED"}</span>
          </div>
          <p className="small">
            Genome {shortHash(challenge.genome_id, 12)} instantiated on a second scenario{challenge.seed != null ? ` (seed ${challenge.seed})` : ""}. Preconditions {challenge.preconditions_met ? "met" : "not met"}.
          </p>
          {challenge.preconditions_checked && (
            <ul className="pre">
              {challenge.preconditions_checked.map((p) => (
                <li key={p.precondition} className={p.met ? "ok" : "nok"}>
                  <span className="tick">{p.met ? "✓" : "✗"}</span> <code>{p.precondition}</code>
                </li>
              ))}
            </ul>
          )}
          <h4>Instantiated schedule</h4>
          <ScheduleList schedule={challenge.instantiated_schedule} highlightIndex={0} />
          <h4>Verdicts on the counterexample</h4>
          <ul className="verdicts compact">
            {challenge.verdicts.map((v) => (
              <li key={v.invariant_id} className={`verdict verdict-${v.result.toLowerCase()}`}>
                <div className="verdict-head">
                  <VerdictPill v={v.result} /> <span className="inv">{v.invariant_id}</span>
                </div>
                <div className="small">{v.explanation}</div>
                {v.missing_data.length > 0 && <div className="small muted">missing signal: {v.missing_data.join(", ")}</div>}
              </li>
            ))}
          </ul>
          {ce && (
            <>
              <h4>Counterexample summary</h4>
              <div className="small">
                branch <code>{ce.branch_id}</code> · trace {shortHash(ce.digest, 14)} · {ce.truth.events.length} truth events · roles {Object.keys(ce.views).join(", ")}
              </div>
              <MiniTimeline ce={ce} witness={ceWitness} />
              {ceDecision && (
                <div className="small">
                  Decision <b>{String(ceDecision.payload.rule_id)}</b> at {tClock(Number(ceDecision.payload.at_ns))} chose <code className="bad">{String(ceDecision.payload.option)}</code> knowing{" "}
                  {(ceDecision.payload.known_event_ids as string[]).join(", ")}; the witness {[...ceWitness].join(", ")} arrived later.
                </div>
              )}
            </>
          )}
          <p className="ceiling">{challenge.claim_ceiling ?? "Transfer is within the shared Event v1 substrate, not across heterogeneous codebases."}</p>
        </section>
      </div>
    </div>
  );
}

function MiniTimeline({ ce, witness }: { ce: NonNullable<ChallengeResult["counterexample"]>; witness: Set<string> }) {
  const roles = Object.keys(ce.views);
  const horizon = ce.clock_end.horizon_ns ?? ce.clock_end.now_ns;
  const W = 520;
  const L = 90;
  const laneH = 34;
  const H = roles.length * laneH + 20;
  const X = (ns: number) => L + (Math.min(horizon, ns) / horizon) * (W - L - 10);
  return (
    <svg className="mini-tl" viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="counterexample timeline">
      {roles.map((r, i) => {
        const y = 10 + i * laneH + laneH / 2;
        return (
          <g key={r}>
            <rect x={0} y={10 + i * laneH} width={W} height={laneH - 2} className="lane-bg" />
            <text x={6} y={y + 4} className="lane-title small">
              {r}
            </text>
            {ce.views[r].events.map((e) => {
              const recv = e.received_time_ns ?? e.event_time_ns;
              const w = witness.has(e.id);
              return (
                <g key={e.id} className={`ev ${w ? "ev-witness" : ""}`}>
                  <title>{`${e.id} ${e.kind}: ${tClock(e.event_time_ns)} → ${tClock(recv)} (lag ${secs(recv - e.event_time_ns)})`}</title>
                  <line x1={X(e.event_time_ns)} x2={X(recv)} y1={y} y2={y} className="lag-line" />
                  <line x1={X(recv)} x2={X(recv)} y1={y - 4} y2={y + 4} className="recv-tick" />
                  {w && <circle cx={X(e.event_time_ns)} cy={y} r={7} className="wit-ring" />}
                  <circle cx={X(e.event_time_ns)} cy={y} r={3.5} className="ev-mark" />
                </g>
              );
            })}
            {ce.decisions
              .filter((d) => String(d.payload.actor).startsWith(r.replace(/_/g, ".").split(".")[0]) || i === 0)
              .map((d) => {
                const at = Number(d.payload.at_ns);
                return (
                  <g key={d.id} className="dec">
                    <line x1={X(at)} x2={X(at)} y1={10 + i * laneH} y2={10 + (i + 1) * laneH - 2} className="dec-line" />
                    <path d={`M${X(at)} ${y - 6}L${X(at) + 6} ${y}L${X(at)} ${y + 6}L${X(at) - 6} ${y}z`} className="dec-mark" />
                  </g>
                );
              })}
          </g>
        );
      })}
    </svg>
  );
}
