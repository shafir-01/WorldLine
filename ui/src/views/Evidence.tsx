import { useEffect, useMemo, useState } from "react";
import type { Aggregate, Authorization, Benchmark, EvidenceKind, EvidenceRecord, LoadedData } from "../types";
import { claimName, humanName, invariantName, requirementName, sortStrategies, STRATEGY_HELP } from "../format";
import { ShortId } from "../components/ShortId";
import { KIND_HELP, KIND_LABEL, KindBadge, KindGlyph, ProducerTag, VerdictGlyph } from "../components/Badges";
import { ContextBar, Ctx, Disclosure, Seg, Tip, ViewIntro } from "../components/Inspect";
import "../inspect.css";

const KINDS: EvidenceKind[] = ["ATTESTED", "PROVED_WITHIN_MODEL", "EMPIRICALLY_VALIDATED", "FAILED", "UNKNOWN"];

interface Props {
  data: LoadedData;
}

interface ClaimGroup {
  claim: string;
  first: number;
  items: { r: EvidenceRecord; i: number }[];
}

export default function Evidence({ data }: Props) {
  const { bundle, authorization, authorizationDenied, benchmark } = data;
  const [which, setWhich] = useState<"authorized" | "denied">("authorized");
  const auth: Authorization = which === "denied" && authorizationDenied ? authorizationDenied : authorization;
  const denied = auth.decision === "DENIED";

  // DENIED reasons look like "integrity:record_edited@1: ..." — the named record index is the broken one.
  const tamperedIdx = useMemo(() => {
    const s = new Set<number>();
    if (denied) for (const r of auth.reasons) for (const m of r.matchAll(/@(\d+)/g)) s.add(Number(m[1]));
    return s;
  }, [auth, denied]);
  const satisfiedIds = new Set(auth.satisfied.map((s) => s.record_id));
  const flaggedIds = new Set([...auth.stale.map((s) => s.record_id), ...auth.downgraded.map((d) => d.record_id)]);

  const groups = useMemo<ClaimGroup[]>(() => {
    const m = new Map<string, ClaimGroup>();
    bundle.records.forEach((r, i) => {
      const g = m.get(r.claim) ?? { claim: r.claim, first: i, items: [] };
      g.items.push({ r, i });
      m.set(r.claim, g);
    });
    return [...m.values()].sort((a, b) => a.first - b.first);
  }, [bundle]);

  const [open, setOpen] = useState<Set<string>>(new Set());
  const tamperedClaim = groups.find((g) => g.items.some((x) => tamperedIdx.has(x.i)))?.claim;
  useEffect(() => {
    if (tamperedClaim) setOpen((o) => new Set([...o, tamperedClaim]));
  }, [tamperedClaim]);
  const showTampered = () => {
    if (!tamperedClaim) return;
    setOpen((o) => new Set([...o, tamperedClaim]));
    requestAnimationFrame(() => {
      const el = document.querySelector(".ix-rec.tampered") as HTMLElement | null;
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
      el?.focus({ preventScroll: true });
    });
  };

  // Requirements: the policy's claim patterns, as the authorized evaluation lists them, plus anything reported missing.
  const requirements = useMemo(() => {
    const pats = new Map<string, { pattern: string; met: Authorization["satisfied"]; missing: boolean }>();
    for (const s of authorization.satisfied) if (!pats.has(s.claim_pattern)) pats.set(s.claim_pattern, { pattern: s.claim_pattern, met: [], missing: false });
    for (const m of auth.missing) pats.set(m.claim_pattern, { pattern: m.claim_pattern, met: [], missing: true });
    for (const s of auth.satisfied) pats.get(s.claim_pattern)?.met.push(s);
    return [...pats.values()];
  }, [authorization, auth]);

  const nKernel = auth.satisfied.filter((s) => s.producer === "kernel").length;
  const tamperIdx = [...tamperedIdx][0];
  const answer = denied ? (
    <>
      It would not be. Record <b>#{tamperIdx ?? "?"}</b> was edited after it was hashed, so the chain no longer verifies and the kernel answers <b className="bad">DENIED</b> before it looks at any requirement.
    </>
  ) : (
    <>
      Every requirement of the <code>{auth.policy}</code> policy is met by typed evidence: {auth.satisfied.length} records across {requirements.length} claim patterns, {nKernel} of them recomputed and attested by the kernel itself. The {bundle.records.length}-record chain is intact
      {bundle.checkpoint ? " and signed" : ""}.
    </>
  );

  return (
    <div className="view ix-view">
      <ViewIntro eyebrow="Inspect · Evidence" question="Why was this release allowed?" answer={answer} />

      <ContextBar label="Evidence context">
        <Seg
          label="Which bundle"
          value={which}
          onChange={setWhich}
          options={[
            { value: "authorized", label: "Real bundle", sub: "authorization.json" },
            { value: "denied", label: "Edited copy", sub: "authorization_denied.json", disabled: !authorizationDenied, title: authorizationDenied ? "Same policy evaluated on bundle_tampered.json" : "authorization_denied.json not present" },
          ]}
        />
        <Ctx k="Policy">
          <code>{auth.policy}</code>
        </Ctx>
        <Ctx k="Artifact">
          <ShortId value={auth.artifact_digest} />
        </Ctx>
        <span className="ix-context-spacer" />
        <span className={`ix-decision-chip ${denied ? "no" : "ok"}`}>
          <VerdictGlyph v={denied ? "FAIL" : "PASS"} size={10} /> {auth.decision}
        </span>
      </ContextBar>

      <section className={`ix-banner ${denied ? "no" : "ok"}`} aria-live="polite">
        <div className="ix-banner-word">
          <VerdictGlyph v={denied ? "FAIL" : "PASS"} size={22} />
          {auth.decision}
        </div>
        <div className="ix-banner-body">
          <ul className="ix-reasons">
            {auth.reasons.map((r) => (
              <li key={r}>{reasonText(r)}</li>
            ))}
          </ul>
          <div className="small muted">
            {auth.bundle_file && <>evaluated on {auth.bundle_file.replace(/\\/g, "/").split("/").pop()}</>}
            {auth.evaluated_at && <> · {auth.evaluated_at.replace(/\.\d+\+00:00$/, "Z").replace("T", " ")}</>}
          </div>
        </div>
        {denied && tamperedClaim && (
          <button className="btn btn-sm" onClick={showTampered}>
            Show the edited record
          </button>
        )}
      </section>

      <section className="panel" aria-labelledby="req-h">
        <div className="ix-sec-head">
          <h2 id="req-h">What the policy requires</h2>
          <span className="small muted">one row per claim pattern the policy asks for; every row must be met</span>
        </div>
        <div className="table-wrap">
          <table className="ix-table ix-req">
            <thead>
              <tr>
                <th>Requirement</th>
                <th>Status</th>
                <th>Satisfied by</th>
                <th>
                  <span className="ix-th-tip">
                    Kind <Tip text={<KindLegend />} label="What the evidence kinds mean" />
                  </span>
                </th>
                <th>
                  <span className="ix-th-tip">
                    Producer <Tip text="kernel: the authorization kernel recomputed this itself and signed it. agent: produced by the pipeline; the kernel recomputes what it can." label="What producer means" />
                  </span>
                </th>
              </tr>
            </thead>
            <tbody>
              {requirements.map((q) => {
                const kinds = [...new Set(q.met.map((s) => s.kind))];
                const producers = [...new Set(q.met.map((s) => s.producer ?? "?"))];
                const claims = [...new Set(q.met.map((s) => s.claim ?? s.claim_pattern))];
                return (
                  <tr key={q.pattern} className={q.met.length ? "" : "ix-req-unmet"}>
                    <td>
                      <b>{requirementName(q.pattern)}</b>
                      <code className="ix-term">{q.pattern}</code>
                    </td>
                    <td>
                      {q.met.length ? (
                        <span className="ix-status ok">
                          <VerdictGlyph v="PASS" size={10} /> Met
                        </span>
                      ) : q.missing ? (
                        <span className="ix-status no">
                          <VerdictGlyph v="FAIL" size={10} /> Missing
                        </span>
                      ) : (
                        <span className="ix-status skip">
                          <VerdictGlyph v="UNKNOWN" size={10} /> Not reached
                        </span>
                      )}
                    </td>
                    <td className="ix-wrap">
                      {q.met.length ? (
                        <>
                          {q.met.length} record{q.met.length === 1 ? "" : "s"}
                          <span className="muted"> · {claims.length > 1 ? `${claims.length} claims (${claims.map((c) => claimName(c).name).slice(0, 2).join(", ")}${claims.length > 2 ? ", …" : ""})` : claimName(claims[0]).name}</span>
                        </>
                      ) : (
                        <span className="muted">{denied ? "integrity failed first" : "nothing in the bundle"}</span>
                      )}
                    </td>
                    <td>
                      <span className="ix-kinds-cell">
                        {kinds.map((k) => (
                          <span key={k} className="ix-kind-mini" title={KIND_HELP[k]}>
                            <KindGlyph kind={k} size={14} /> {KIND_LABEL[k]}
                          </span>
                        ))}
                      </span>
                    </td>
                    <td>
                      <span className="ix-kinds-cell">
                        {producers.map((p) => (
                          <ProducerTag key={p} producer={p} />
                        ))}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel" aria-labelledby="chain-h">
        <div className="ix-sec-head">
          <h2 id="chain-h">The evidence, grouped by claim</h2>
          <span className="small muted">
            {bundle.records.length} records in {groups.length} claims · each record links to the one before it by hash
          </span>
          <span className="ix-context-spacer" />
          <button className="btn btn-sm btn-ghost" onClick={() => setOpen(open.size ? new Set() : new Set(groups.map((g) => g.claim)))}>
            {open.size ? "Collapse all" : "Expand all"}
          </button>
        </div>
        <ul className="ix-claims">
          {groups.map((g) => {
            const cn = claimName(g.claim);
            const kinds = [...new Set(g.items.map((x) => x.r.kind))];
            const producers = [...new Set(g.items.map((x) => x.r.producer))];
            const hasTamper = g.items.some((x) => tamperedIdx.has(x.i));
            const nSat = g.items.filter((x) => satisfiedIds.has(x.r.id)).length;
            return (
              <li key={g.claim} className={hasTamper ? "tampered" : ""}>
                <Disclosure
                  open={open.has(g.claim)}
                  onToggle={(o) => setOpen((s) => {
                    const n = new Set(s);
                    if (o) n.add(g.claim);
                    else n.delete(g.claim);
                    return n;
                  })}
                  summary={
                    <span className="ix-claim-sum">
                      <span className="ix-claim-group">{cn.group}</span>
                      <b>{cn.name}</b>
                    </span>
                  }
                  meta={
                    <span className="ix-claim-meta">
                      {hasTamper && <span className="flag flag-tag-tampered">edited</span>}
                      {!hasTamper && nSat > 0 && <span className="ix-sat">satisfies policy</span>}
                      {kinds.map((k) => (
                        <span key={k} title={KIND_LABEL[k]}>
                          <KindGlyph kind={k} size={14} />
                        </span>
                      ))}
                      <span className="muted">
                        {g.items.length} record{g.items.length === 1 ? "" : "s"} · {producers.map((p) => (p === "kernel" ? "kernel" : "agent")).join(" + ")}
                      </span>
                    </span>
                  }
                >
                  <code className="ix-term">{g.claim}</code>
                  <ol className="ix-recs">
                    {g.items.map(({ r, i }) => (
                      <RecordRow key={i} r={r} i={i} prevHash={bundle.links[i]?.prev_hash} hash={bundle.links[i]?.hash} tampered={tamperedIdx.has(i)} satisfied={satisfiedIds.has(r.id)} flagged={flaggedIds.has(r.id)} />
                    ))}
                  </ol>
                </Disclosure>
              </li>
            );
          })}
        </ul>
        {bundle.checkpoint && (
          <div className="ix-seal small">
            <KindGlyph kind="ATTESTED" size={16} />
            <span>
              Sealed {bundle.checkpoint.sealed_at.replace("T", " ").replace(/\.\d+.*$/, "")} over records {bundle.checkpoint.first_index}–{bundle.checkpoint.last_index}
            </span>
            <ShortId value={bundle.checkpoint.root} label="root" />
            <ShortId value={bundle.checkpoint.signer_key_id} label="key" />
            <ShortId value={bundle.checkpoint.signature} label="signature" />
          </div>
        )}
      </section>

      {benchmark && <BenchmarkCard b={benchmark} />}
    </div>
  );
}

function reasonText(r: string): string {
  const edited = r.match(/^integrity:record_edited@(\d+):?\s*(.*)$/);
  if (edited) return `Record #${edited[1]} was changed after it was hashed (${edited[2] || "content does not match its hash"}).`;
  const all = r.match(/^all (\d+) requirement\(s\) met by (\d+) record\(s\)$/);
  if (all) return `All ${all[1]} requirements are met, by ${all[2]} records.`;
  return r;
}

function KindLegend() {
  return (
    <ul className="ix-tip-list">
      {KINDS.map((k) => (
        <li key={k}>
          <KindGlyph kind={k} size={12} /> <b>{KIND_LABEL[k]}</b>: {KIND_HELP[k]}
        </li>
      ))}
    </ul>
  );
}

function resultSummary(r: EvidenceRecord): string {
  const res = r.result ?? {};
  if (typeof res.explanation === "string") return `${res.result ? `${String(res.result)}: ` : ""}${res.explanation}`;
  if (typeof res.passed === "number") return `${res.passed} of ${String(res.tests)} tests passed, ${String(res.failures)} failures.`;
  if (typeof res.minimal_size === "number") return `${String(res.original_size)} changes reduced to ${String(res.minimal_size)}; 1-minimal ${res.one_minimal_verified ? "verified" : "not verified"}.`;
  if (res.primary && typeof res.primary === "object") {
    const p = res.primary as Record<string, unknown>;
    return `Primary ${String(p.scenario_id ?? "")} ${p.reproduced ? "reproduced" : "not reproduced"}; ${Array.isArray(res.controls) ? res.controls.length : 0} control(s).`;
  }
  if (res.guided && res.random) {
    const g = res.guided as Record<string, number>;
    const q = res.random as Record<string, number>;
    return `Guided: ${g.n_failing} of ${g.n_nodes} broke; random: ${q.n_failing} of ${q.n_nodes}.`;
  }
  const keys = Object.keys(res);
  return keys.length ? `Fields: ${keys.slice(0, 5).map(humanName).join(", ")}${keys.length > 5 ? ", …" : ""}.` : "No result body.";
}

function RecordRow({ r, i, prevHash, hash, tampered, satisfied, flagged }: { r: EvidenceRecord; i: number; prevHash?: string; hash?: string; tampered: boolean; satisfied: boolean; flagged: boolean }) {
  return (
    <li className={`ix-rec ${tampered ? "tampered" : ""} ${flagged ? "flagged" : ""}`} tabIndex={tampered ? -1 : undefined} aria-label={tampered ? `Record ${i}, edited` : undefined}>
      <div className="ix-rec-head">
        <span className="ix-rec-idx">#{i}</span>
        <KindBadge kind={r.kind} />
        <ProducerTag producer={r.producer} />
        {tampered && <span className="flag flag-tag-tampered">edited after hashing</span>}
        {!tampered && satisfied && <span className="ix-sat">counts toward the policy</span>}
        <span className="ix-rec-proc small muted">
          via <code>{r.procedure}</code>
        </span>
      </div>
      <p className="small ix-rec-sum">{resultSummary(r)}</p>
      <Disclosure summary="Raw record">
        <div className="ix-ids">
          <ShortId value={r.id} label="id" />
          <ShortId value={r.subject} label="subject" />
          <ShortId value={r.inputs_hash} label="inputs" />
          {prevHash && <ShortId value={prevHash} label="prev" />}
          {hash && <ShortId value={hash} label="link" />}
          <span className="small muted">derived {r.derived_at}</span>
        </div>
        {r.assumptions.length > 0 && <p className="small muted">Assumes: {r.assumptions.join("; ")}.</p>}
        <pre className="json">{JSON.stringify(r.result, null, 2)}</pre>
      </Disclosure>
    </li>
  );
}

// ---------------------------------------------------------------- benchmark

const EXPLORE_METRICS: { key: string; label: string; digits: number; prefix?: string }[] = [
  { key: "n_nodes", label: "Worlds run", digits: 1 },
  { key: "n_failing", label: "Broke a rule", digits: 1 },
  { key: "failing_rate_per_100", label: "Broke per 100", digits: 1 },
  { key: "first_decision_fail_index", label: "First wrong decision at", digits: 1, prefix: "#" },
  { key: "wall_ms", label: "Wall ms", digits: 0 },
];
const MIN_METRICS: { key: string; label: string; digits: number }[] = [
  { key: "original_size", label: "Changes before", digits: 1 },
  { key: "minimal_size", label: "Changes after", digits: 1 },
  { key: "oracle_calls", label: "Runs needed", digits: 1 },
  { key: "wall_ms", label: "Wall ms", digits: 0 },
];

function fmtAgg(a: Aggregate | boolean | number | undefined, digits: number, prefix = ""): React.ReactNode {
  if (a == null || typeof a !== "object") return "—";
  const f = (v: number | null) => (v == null ? "—" : `${prefix}${Number.isInteger(v) ? v : v.toFixed(digits)}`);
  return (
    <>
      {f(a.mean)} <span className="muted">/ {f(a.median)}</span>
      {a.n_null > 0 && (
        <span className="muted" title={`${a.n_null} of ${a.n + a.n_null} seeds found none within budget`}>
          {" "}
          ({a.n_null} none)
        </span>
      )}
    </>
  );
}

function MetricHead({ label, k, def, num = true }: { label: string; k: string; def: (k: string) => string; num?: boolean }) {
  const d = def(k);
  return (
    <th className={num ? "num" : ""}>
      <span className="ix-th-tip">
        {label}
        {d && (
          <Tip
            text={
              <>
                <code>{k}</code>: {d}
              </>
            }
            label={`Definition of ${label}`}
          />
        )}
      </span>
    </th>
  );
}

function BenchmarkCard({ b }: { b: Benchmark }) {
  const strategies = sortStrategies(Object.keys(b.exploration.aggregate));
  const def = (k: string) => b.definitions[k] ?? "";
  const minAgg = b.minimization.aggregate;
  const allVerified = minAgg.one_minimal_verified_all === true;
  return (
    <section className="panel" aria-labelledby="bench-h">
      <div className="ix-sec-head">
        <h2 id="bench-h">Does it hold beyond one seed?</h2>
        <span className="small muted">
          {b.seeds.length} seeds on <code>{b.scenario_id}</code>, budget {b.budget}, target {invariantName(b.target_invariant).toLowerCase()} · mean <span aria-hidden>/</span> median
          <Tip text={def("aggregate") || "Mean and median over seeds."} label="How the aggregates are computed" />
        </span>
      </div>
      <div className="bench-grid">
        <div>
          <h3 className="ix-h">Exploration: {strategies.join(" vs ")}</h3>
          <div className="table-wrap">
            <table className="table ix-compare">
              <thead>
                <tr>
                  <th>Strategy</th>
                  {EXPLORE_METRICS.map((m) => (
                    <MetricHead key={m.key} label={m.label} k={m.key} def={def} />
                  ))}
                </tr>
              </thead>
              <tbody>
                {strategies.map((s) => (
                  <tr key={s}>
                    <td>
                      <b title={STRATEGY_HELP[s]}>{s.charAt(0).toUpperCase() + s.slice(1)}</b>
                    </td>
                    {EXPLORE_METRICS.map((m) => (
                      <td key={m.key} className="num">
                        {fmtAgg(b.exploration.aggregate[s][m.key], m.digits, m.prefix)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div>
          <h3 className="ix-h">Minimization over {b.minimization.cases.length} failing worlds</h3>
          <div className="table-wrap">
            <table className="table ix-compare">
              <thead>
                <tr>
                  {MIN_METRICS.map((m) => (
                    <MetricHead key={m.key} label={m.label} k={m.key} def={def} />
                  ))}
                  <MetricHead label="1-minimal" k="one_minimal_verified" def={def} num={false} />
                </tr>
              </thead>
              <tbody>
                <tr>
                  {MIN_METRICS.map((m) => (
                    <td key={m.key} className="num">
                      {fmtAgg(minAgg[m.key] as Aggregate | undefined, m.digits)}
                    </td>
                  ))}
                  <td>
                    <span className={`ix-status ${allVerified ? "ok" : "no"}`}>
                      <VerdictGlyph v={allVerified ? "PASS" : "FAIL"} size={10} /> {allVerified ? `all ${b.minimization.cases.length}` : "not all"}
                    </span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
      <p className="ceiling">
        <b>Claim ceiling:</b> {b.claim_ceiling}
      </p>
      <Disclosure summary="All definitions and the environment">
        <dl className="kv small">
          {Object.entries(b.definitions).map(([k, v]) => (
            <Def key={k} k={k} v={v} />
          ))}
        </dl>
        <div className="small muted">
          {Object.entries(b.environment)
            .filter(([k]) => k !== "python_full")
            .map(([k, v]) => `${k}: ${v}`)
            .join(" · ")}
        </div>
      </Disclosure>
    </section>
  );
}

function Def({ k, v }: { k: string; v: string }) {
  return (
    <>
      <dt>
        <code>{k}</code>
      </dt>
      <dd>{v}</dd>
    </>
  );
}
