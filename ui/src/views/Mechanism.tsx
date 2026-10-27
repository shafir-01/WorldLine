import type { ReactNode } from "react";
import type { ChallengeResult, LoadedData } from "../types";
import { challengeKey, type TraceEntry } from "../data";
import { humanName, interventionPlain, interventionText, invariantHelp, invariantName, mmss, opName, mechanismPlain, parseDecisionExplanation, preconditionText, secs, shortBranch, verdictSentence } from "../format";
import { ScheduleList } from "../components/Schedule";
import { KindBadge, VerdictGlyph, VerdictPill } from "../components/Badges";
import { ShortId } from "../components/ShortId";
import { Check, ContextBar, Ctx, Disclosure, Tip, ViewIntro } from "../components/Inspect";
import "../inspect.css";

interface Props {
  data: LoadedData;
  registry: Map<string, TraceEntry>;
  onOpenTimeline: (key: string) => void;
}

const PRECONDITION_HELP: Record<string, string> = {
  has_decision_rule: "Some actor makes its choice with an explicit rule evaluated at a fixed time.",
  decision_rule_depends_on_a_corrected_kind: "That rule counts a kind of report (sightings, threat reports) that a later message can correct.",
  event_received_by_decision_role_before_decision: "Normally the correction reaches the deciding role before the decision, so holding it back changes what the decider knows.",
  "exists_kind:correction:with_payload_field:corrects": "A correction event exists and names the report it corrects in its 'corrects' field.",
};

const FLAG_HELP = {
  one_minimal_verified: "Checked independently after ddmin: the minimal schedule still fails, and removing any single change makes it pass.",
  self_explaining: "The kept change targets an event that appears in the failure witness, so the cause is visible in the evidence itself.",
};

function missingIds(explanation: string): string[] {
  return explanation.match(/had not received \[([^\]]*)\]/)?.[1]?.match(/'([^']+)'/g)?.map((s) => s.slice(1, -1)) ?? [];
}

export default function Mechanism({ data, registry, onOpenTimeline }: Props) {
  const { minimal, genome, challenge } = data;
  const minimalKeys = new Set(minimal.minimal.map(interventionText));
  const kept = minimal.minimal[0];
  const results = [challenge.primary, ...challenge.controls];
  const minimalHasTrace = registry.has(minimal.minimal_branch_id);
  const primaryKey = registry.has(challengeKey(challenge.primary.scenario_id)) ? challengeKey(challenge.primary.scenario_id) : null;
  const controlsHeld = challenge.controls.every((c) => c.preconditions_met && !c.reproduced);

  const answer = (
    <>
      {minimal.minimal.length === 1 ? "One change" : `${minimal.minimal.length} changes`} out of {minimal.original.length} explain{minimal.minimal.length === 1 ? "s" : ""} the failure:{" "}
      <b>{kept ? interventionPlain(kept).toLowerCase() : "none"}</b>. As a pattern it {challenge.primary.reproduced ? "broke" : "did not break"} <code>{challenge.primary.scenario_id}</code>
      {challenge.controls.length > 0 && (controlsHeld ? <> and left the immune twin intact, so it transfers and is specific.</> : <>, but a control did not behave as expected.</>)}
      {challenge.controls.length === 0 && "."}
    </>
  );

  return (
    <div className="view ix-view">
      <ViewIntro eyebrow="Inspect · Mechanism" question="What is the single cause, and does it transfer?" answer={answer} />

      <ContextBar label="Mechanism context">
        <Ctx k="Genome">
          <ShortId value={genome.id} />
        </Ctx>
        <Ctx k="From">
          <code>{genome.origin.scenario_id}</code> <span className="muted">seed {genome.origin.seed}</span>
        </Ctx>
        <Ctx k="Breaks">{invariantName(minimal.target_invariant)}</Ctx>
        <span className="ix-context-spacer" />
        <nav className="ix-steplinks" aria-label="Steps">
          <a href="#mx-1" onClick={jump("mx-1")}>
            1 Minimize
          </a>
          <a href="#mx-2" onClick={jump("mx-2")}>
            2 Abstract
          </a>
          <a href="#mx-3" onClick={jump("mx-3")}>
            3 Challenge
          </a>
        </nav>
      </ContextBar>

      <ol className="ix-three">
        {/* ------------------------------------------------ 1. minimize */}
        <Step n={1} id="mx-1" title="Minimize" term="ddmin" lede="Remove changes one at a time until only what is necessary remains.">
          <div className="ix-big">
            <span className="ix-big-n">{minimal.original.length}</span>
            <span className="ix-big-arrow" aria-hidden>
              →
            </span>
            <span className="ix-big-n hl">{minimal.minimal.length}</span>
            <span className="ix-big-l">
              change{minimal.original.length === 1 ? "" : "s"} tried, {minimal.minimal.length} kept
            </span>
          </div>
          <ul className="ix-ivs" aria-label="Changes in the original failing world">
            {minimal.original.map((iv, i) => {
              const isKept = minimalKeys.has(interventionText(iv));
              return (
                <li key={i} className={isKept ? "kept" : "removed"}>
                  <span className={`ix-op op-${iv.op}`}>{opName(iv.op)}</span>
                  <span className="ix-iv-text">{interventionPlain(iv)}</span>
                  <span className="ix-iv-status">{isKept ? "kept: the cause" : "removed: not needed"}</span>
                </li>
              );
            })}
          </ul>

          <h3 className="ix-h">How it got there</h3>
          <ol className="ix-stepper" aria-label="Reduction steps">
            {minimal.steps.map((s, i) => (
              <li key={i} className={s.fails ? "fails" : "passes"}>
                <span className="ix-stepper-dot" aria-hidden>
                  <VerdictGlyph v={s.fails ? "FAIL" : "PASS"} size={12} />
                </span>
                <span className="ix-stepper-l">
                  <b>
                    {s.size} change{s.size === 1 ? "" : "s"}
                  </b>
                  <span>{s.fails ? "still breaks" : "holds"}</span>
                </span>
              </li>
            ))}
          </ol>

          <ul className="ix-checklist">
            <Check ok={minimal.one_minimal_verified} label="Removing anything else makes it pass" term="one_minimal_verified" help={FLAG_HELP.one_minimal_verified} />
            <Check ok={minimal.self_explaining} label="The kept change shows up in the evidence" term="self_explaining" help={FLAG_HELP.self_explaining} />
          </ul>

          <div className="ix-actions">
            <button className="btn btn-sm btn-primary" onClick={() => onOpenTimeline(minimal.minimal_branch_id)} disabled={!minimalHasTrace}>
              Open the minimal counterexample →
            </button>
          </div>

          <Disclosure summary="Details" meta={`${minimal.oracle_calls} runs, ${minimal.cache_hits} cached`}>
            <p className="small" title={minimal.explanation}>
              {verdictSentence({ invariant_id: minimal.target_invariant, result: "FAIL", explanation: minimal.explanation })}
            </p>
            <div className="ix-witness small">
              <span className="muted">Witness</span>
              {minimal.witness.map((w) => (
                <code key={w} className="wit">
                  {w}
                </code>
              ))}
            </div>
            <dl className="kv">
              <dt>
                runs <Tip text="oracle_calls: distinct schedules ddmin executed (cache hits excluded)." />
              </dt>
              <dd>{minimal.oracle_calls}</dd>
              <dt>
                cached <Tip text="cache_hits: schedules answered from the cache without running." />
              </dt>
              <dd>{minimal.cache_hits}</dd>
              <dt>started from</dt>
              <dd>
                <BranchLink id={minimal.branch_id} registry={registry} onOpen={onOpenTimeline} /> ({minimal.strategy} run)
              </dd>
              <dt>ends as</dt>
              <dd>
                <BranchLink id={minimal.minimal_branch_id} registry={registry} onOpen={onOpenTimeline} />
              </dd>
              <dt>ids</dt>
              <dd className="ix-ids">
                <ShortId value={minimal.minimal_schedule_hash} label="schedule" />
                <ShortId value={minimal.trace_digest} label="trace" />
              </dd>
            </dl>
            {minimal.causal_diff && (
              <>
                <h5>What changed versus the baseline</h5>
                <ul className="plain ix-plain">
                  {minimal.causal_diff.decisions.map((d) => (
                    <li key={d.rule_id} className="small">
                      {humanName(d.actor)} · {humanName(d.rule_id)}: <code>{d.option_a}</code> → <code className="bad">{d.option_b}</code>
                      {d.known_only_in_a.length > 0 && <div className="muted">known only in the baseline: {d.known_only_in_a.join(", ")}</div>}
                    </li>
                  ))}
                  {Object.entries(minimal.causal_diff.views).flatMap(([role, v]) =>
                    v.received_time_moved.map((m) => (
                      <li key={`${role}/${m.event_id}`} className="small">
                        <code>{m.event_id}</code> for {humanName(role)}: {mmss(m.received_a_ns)} → {mmss(m.received_b_ns)} <span className="bad">(+{secs(m.delta_ns)})</span>
                      </li>
                    )),
                  )}
                </ul>
              </>
            )}
            <h5>Why this failing world was chosen</h5>
            <p className="small muted">{minimal.selection_rule}</p>
            <div className="table-wrap">
              <table className="table small">
                <thead>
                  <tr>
                    <th>candidate</th>
                    <th className="num">changes</th>
                    <th className="num">minimal</th>
                    <th className="num">runs</th>
                    <th>in witness</th>
                  </tr>
                </thead>
                <tbody>
                  {minimal.candidates_considered.map((c) => (
                    <tr key={c.branch_id} className={c.branch_id === minimal.branch_id ? "active" : ""}>
                      <td>
                        <BranchLink id={c.branch_id} registry={registry} onOpen={onOpenTimeline} />
                      </td>
                      <td className="num">{c.original_size}</td>
                      <td className="num">{c.minimal_size}</td>
                      <td className="num">{c.oracle_calls}</td>
                      <td>{c.self_explaining ? "yes" : "no"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Disclosure>
        </Step>

        {/* ------------------------------------------------ 2. abstract */}
        <Step n={2} id="mx-2" title="Abstract" term="genome.extract" lede="Replace names and timestamps with roles and positions, so the cause becomes a pattern.">
          <p className="ix-sentence">{(genome.mechanism[0] && mechanismPlain(genome.mechanism[0])) ?? capFirst(genome.mechanism_sentence ?? "")}</p>
          <p className="small muted">Then the decision no longer matches what really happened.</p>
          <div className="ix-meta-row">
            <KindBadge kind={genome.evidence_kind} />
            <span className="small muted">
              breaks <b>{invariantName(genome.violated_invariant)}</b> <Tip text={`${genome.violated_invariant}: ${invariantHelp(genome.violated_invariant)}`} />
            </span>
          </div>

          <h3 className="ix-h">It needs {genome.preconditions.length} things to be true</h3>
          <ul className="ix-checklist">
            {genome.preconditions.map((p) => (
              <Check key={p} ok label={preconditionText(p)} term={p} help={PRECONDITION_HELP[p] ?? p} />
            ))}
          </ul>
          <p className="small muted">All hold in the origin scenario {genome.origin.scenario_id}; step 3 checks them elsewhere.</p>

          <Disclosure summary="Details" meta="selectors, witness shape, origin">
            {genome.mechanism_sentence && (
              <>
                <h5>As recorded</h5>
                <p className="small">{capFirst(genome.mechanism_sentence)}</p>
              </>
            )}
            <h5>Abstract mechanism</h5>
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
            <h5>Witness shape</h5>
            <dl className="kv">
              {Object.entries(genome.witness_shape).map(([k, v]) => (
                <Pair key={k} k={k} v={String(v)} />
              ))}
            </dl>
            <h5>Origin</h5>
            <dl className="kv">
              <dt>scenario</dt>
              <dd>
                {genome.origin.scenario_id} · seed {genome.origin.seed}
              </dd>
              <dt>world</dt>
              <dd>
                <BranchLink id={genome.origin.branch_id} registry={registry} onOpen={onOpenTimeline} />
              </dd>
              <dt>ids</dt>
              <dd className="ix-ids">
                <ShortId value={genome.id} label="genome" />
                <ShortId value={genome.origin.trace_digest} label="trace" />
                <ShortId value={genome.origin.code_commit} label="commit" />
              </dd>
            </dl>
            <p className="ceiling">{genome.claim_ceiling ?? "Transfer is within the shared Event v1 substrate, not across heterogeneous codebases."}</p>
          </Disclosure>
        </Step>

        {/* ------------------------------------------------ 3. challenge */}
        <Step n={3} id="mx-3" title="Challenge" term="genome.challenge" lede="Plant the pattern in other scenarios: it should break a look-alike and spare a twin built to be immune.">
          <div className={`ix-outcome ${challenge.success ? "ok" : "nok"}`}>
            <VerdictGlyph v={challenge.success ? "PASS" : "FAIL"} size={14} />
            <span>
              <b>{challenge.success ? "Transfers, and is specific." : "Did not behave as predicted."}</b>{" "}
              <span className="muted">
                {results.length} scenario{results.length === 1 ? "" : "s"} tried
              </span>
            </span>
          </div>
          {results.map((r, i) => (
            <ChallengeCard key={r.scenario_id} r={r} kind={i === 0 ? "primary" : "control"} traceKey={registry.has(challengeKey(r.scenario_id)) ? challengeKey(r.scenario_id) : null} onOpenTimeline={onOpenTimeline} />
          ))}
          {primaryKey && (
            <div className="ix-actions">
              <button className="btn btn-sm btn-primary" onClick={() => onOpenTimeline(primaryKey)}>
                Open the counterexample timeline →
              </button>
            </div>
          )}
          <p className="ceiling small">{challenge.claim_ceiling ?? "Transfer is within the shared Event v1 substrate, not across heterogeneous codebases."}</p>
        </Step>
      </ol>
    </div>
  );
}

const capFirst = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

function jump(id: string) {
  return (e: React.MouseEvent) => {
    e.preventDefault();
    const el = document.getElementById(id);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "start" });
    (el.querySelector("h2") as HTMLElement | null)?.focus({ preventScroll: true });
  };
}

function Step({ n, id, title, term, lede, children }: { n: number; id: string; title: string; term: string; lede: string; children: ReactNode }) {
  return (
    <li className="ix-step panel" id={id} aria-labelledby={`${id}-h`}>
      <div className="ix-step-head">
        <span className="ix-step-n" aria-hidden>
          {n}
        </span>
        <div>
          <h2 id={`${id}-h`} tabIndex={-1}>
            <span className="sr-only">Step {n}: </span>
            {title} <code className="ix-term">{term}</code>
          </h2>
          <p className="ix-step-lede">{lede}</p>
        </div>
      </div>
      {children}
    </li>
  );
}

function Pair({ k, v }: { k: string; v: string }) {
  return (
    <>
      <dt>{k}</dt>
      <dd>{v}</dd>
    </>
  );
}

function BranchLink({ id, registry, onOpen }: { id: string; registry: Map<string, TraceEntry>; onOpen: (k: string) => void }) {
  return registry.has(id) ? (
    <button className="link" onClick={() => onOpen(id)} title="Open in Timeline">
      {shortBranch(id)}
    </button>
  ) : (
    <code title="no trace exported">{shortBranch(id)}</code>
  );
}

function ChallengeCard({ r, kind, traceKey, onOpenTimeline }: { r: ChallengeResult; kind: "primary" | "control"; traceKey: string | null; onOpenTimeline: (k: string) => void }) {
  // For a negative control the desired outcome is NOT reproduced (with preconditions met): the mechanism is specific.
  const expected = kind === "primary" ? r.reproduced : r.preconditions_met && !r.reproduced;
  const checks = Object.entries(r.preconditions_report);
  const met = checks.filter(([, v]) => v).length;
  const p = parseDecisionExplanation(r.explanation);
  const missing = missingIds(r.explanation);
  const headline = kind === "primary" ? (r.reproduced ? "Broke, as predicted" : "Did not break") : !r.preconditions_met ? "Pattern does not apply" : r.reproduced ? "Broke (unexpected)" : "Held, as it should";
  const reason: ReactNode =
    r.reproduced && p.chose ? (
      <>
        The {humanName(p.actor ?? "decider")} chose <b>{p.chose}</b> where the truth called for <b>{p.truth}</b>
        {missing.length ? `, without ${missing.length} event${missing.length === 1 ? "" : "s"} it had not yet heard` : ""}.
      </>
    ) : !r.preconditions_met ? (
      <>The scenario lacks what the pattern needs, so it could not be planted.</>
    ) : (
      <>The pattern was planted with every precondition met, yet the decision still matched the truth: this twin is built to tolerate the delay.</>
    );
  return (
    <article className={`ix-ch ${kind} ${expected ? "expected" : "unexpected"}`}>
      <header className="ix-ch-head">
        <span className="ix-ch-kind">{kind === "primary" ? "Look-alike" : "Immune twin"}</span>
        <code>{r.scenario_id}</code>
      </header>
      <div className="ix-ch-outcome">
        <VerdictGlyph v={expected ? "PASS" : "FAIL"} size={12} />
        <b>{headline}</b>
      </div>
      <p className="small ix-ch-reason">{reason}</p>
      <div className="ix-ch-meta small">
        <Tip
          text={
            <ul className="ix-tip-list">
              {checks.map(([name, ok]) => (
                <li key={name}>
                  {ok ? "✓" : "✗"} {preconditionText(name)}
                </li>
              ))}
            </ul>
          }
        >
          <span className="ix-tip-text">
            {met}/{checks.length} preconditions met
          </span>
        </Tip>
        {r.target_rule && <span className="muted">rule {humanName(r.target_rule)}</span>}
      </div>
      <Disclosure summary="Planted change and verdicts">
        <ol className="ix-changes">
          {r.instantiated_schedule.map((iv, i) => (
            <li key={i}>
              <span className={`ix-op op-${iv.op}`}>{opName(iv.op)}</span> {interventionPlain(iv)}
            </li>
          ))}
        </ol>
        <ScheduleList schedule={r.instantiated_schedule} />
        <ul className="ix-verdicts">
          {r.verdicts.map((v) => (
            <li key={v.invariant_id} className={`ix-verdict v-${v.result.toLowerCase()}`}>
              <div className="ix-verdict-head">
                <VerdictPill v={v.result} /> <b>{invariantName(v.invariant_id)}</b>
              </div>
              <p className="small" title={v.explanation}>
                {verdictSentence(v)}
              </p>
            </li>
          ))}
        </ul>
        {r.counterexample_digest && <ShortId value={r.counterexample_digest} label="counterexample trace" />}
      </Disclosure>
      {traceKey && kind !== "primary" && (
        <button className="btn btn-sm" onClick={() => onOpenTimeline(traceKey)}>
          Open timeline →
        </button>
      )}
    </article>
  );
}
