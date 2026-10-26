import { useState } from "react";
import type { Aggregate, Authorization, Benchmark, Bundle, EvidenceKind, EvidenceRecord, LoadedData } from "../types";
import { shortHash } from "../format";
import { KIND_HELP, KindBadge, KindGlyph, ProducerTag } from "../components/Badges";

const KINDS: EvidenceKind[] = ["PROVED_WITHIN_MODEL", "EMPIRICALLY_VALIDATED", "ATTESTED", "FAILED", "UNKNOWN"];

interface Props {
  data: LoadedData;
}

export default function Evidence({ data }: Props) {
  const { bundle, authorization, authorizationDenied, benchmark } = data;
  const [which, setWhich] = useState<"authorized" | "denied">("authorized");
  const [open, setOpen] = useState<string | null>(null);
  const auth: Authorization = which === "denied" && authorizationDenied ? authorizationDenied : authorization;
  const flagged = new Map<string, string>();
  for (const s of auth.stale) flagged.set(s.record_id, "stale");
  for (const d of auth.downgraded) flagged.set(d.record_id, "downgraded");
  for (const s of auth.satisfied) if (!flagged.has(s.record_id)) flagged.set(s.record_id, "satisfied");
  // DENIED reasons look like "integrity:record_edited@1: ..." — flag the named record index on the chain.
  const tamperedIdx = new Set<number>();
  if (auth.decision === "DENIED") for (const r of auth.reasons) for (const m of r.matchAll(/@(\d+)/g)) tamperedIdx.add(Number(m[1]));

  return (
    <div className="view">
      <header className="view-head">
        <div>
          <h1>Evidence</h1>
          <p className="lede">
            Every claim is typed by certainty and chained by hash. The kernel is the only producer allowed to emit <b>ATTESTED</b>; agent-produced results are recomputed where possible. Authorization reads only the bundle, the policy
            and the public key: edit one byte of a record and the same policy answers DENIED.
          </p>
        </div>
        <div className="legend legend-col">
          {KINDS.map((k) => (
            <span key={k} title={KIND_HELP[k]}>
              <KindGlyph kind={k} size={16} /> {k}
            </span>
          ))}
        </div>
      </header>

      <div className="split evidence-split">
        <section className="panel chain-panel">
          <div className="card-head">
            <h2>Bundle</h2>
            <span className="small muted">
              <code>{bundle.format}</code> · {bundle.records.length} records
              {bundle.checkpoint && (
                <>
                  {" "}
                  · sealed {bundle.checkpoint.sealed_at} by <code>{bundle.checkpoint.signer_key_id}</code>
                </>
              )}
            </span>
          </div>
          <ol className="chain">
            <li className="chain-genesis">
              <span className="hash">genesis {bundle.links[0] ? shortHash(bundle.links[0].prev_hash, 12) : ""}</span>
            </li>
            {bundle.records.map((r, i) => (
              <RecordBlock key={`${r.id}-${i}`} r={r} i={i} link={bundle.links[i]} open={open === `${i}`} onToggle={() => setOpen(open === `${i}` ? null : `${i}`)} flag={tamperedIdx.has(i) ? "tampered" : flagged.get(r.id)} />
            ))}
            {bundle.checkpoint && (
              <li className="chain-checkpoint">
                <div className="block-head">
                  <KindGlyph kind="ATTESTED" size={16} /> <b>checkpoint</b>{" "}
                  <span className="hash" title={bundle.checkpoint.root}>
                    root {shortHash(bundle.checkpoint.root, 16)}
                  </span>
                  <span className="small muted">
                    records {bundle.checkpoint.first_index}–{bundle.checkpoint.last_index}
                  </span>
                </div>
                <div className="small muted">
                  signature {bundle.checkpoint.signature.slice(0, 28)}… · key {bundle.checkpoint.signer_key_id}
                </div>
              </li>
            )}
          </ol>
        </section>

        <aside className="panel auth-panel">
          <div className="card-head">
            <h2>Authorization</h2>
            <div className="seg" role="tablist" aria-label="authorization case">
              <button role="tab" aria-selected={which === "authorized"} className={`seg-btn ${which === "authorized" ? "active" : ""}`} onClick={() => setWhich("authorized")}>
                <b className="good">AUTHORIZED</b> <span className="muted">authorization.json</span>
              </button>
              <button
                role="tab"
                aria-selected={which === "denied"}
                className={`seg-btn ${which === "denied" ? "active" : ""}`}
                disabled={!authorizationDenied}
                title={authorizationDenied ? "Same policy evaluated on bundle_tampered.json" : "authorization_denied.json not present"}
                onClick={() => setWhich("denied")}
              >
                <b className="bad">DENIED</b> <span className="muted">authorization_denied.json</span>
              </button>
            </div>
          </div>
          <div className={`decision decision-${auth.decision.toLowerCase()}`}>
            <div className="decision-word">{auth.decision}</div>
            <div className="small">
              policy <code>{auth.policy}</code> · artifact <code title={auth.artifact_digest}>{shortHash(auth.artifact_digest, 12)}</code>
              {auth.bundle_file && (
                <>
                  {" "}
                  · bundle <code>{auth.bundle_file.replace(/\\/g, "/").split("/").pop()}</code>
                </>
              )}
              {auth.evaluated_at && <> · evaluated {auth.evaluated_at.replace(/\.\d+\+00:00$/, "Z")}</>}
            </div>
          </div>
          <h4>Reasons</h4>
          <ul className="reasons">
            {auth.reasons.map((r) => (
              <li key={r}>
                <code className={auth.decision === "DENIED" ? "bad" : "good"}>{r}</code>
              </li>
            ))}
          </ul>
          <Section title="Satisfied" tone="good" items={auth.satisfied.map((s) => ({ key: `${s.claim_pattern}:${s.record_id}`, node: <SatisfiedRow s={s} /> }))} />
          <Section title="Missing" tone="bad" items={auth.missing.map((m) => ({ key: m.claim_pattern, node: <>{`${m.claim_pattern}${m.min_kind ? ` needs ${m.min_kind}` : ""}${m.note ? ` — ${m.note}` : ""}`}</> }))} />
          <Section title="Stale" tone="bad" items={auth.stale.map((s) => ({ key: s.record_id, node: <>{`${s.claim ?? shortHash(s.record_id, 10)} age ${s.age_s ?? "?"}s > ${s.max_age_s ?? "?"}s`}</> }))} />
          <Section title="Downgraded" tone="bad" items={auth.downgraded.map((d) => ({ key: d.record_id, node: <>{`${shortHash(d.record_id, 10)}: ${d.from ?? "?"} → ${d.to ?? "?"}${d.reason ? ` — ${d.reason}` : ""}`}</> }))} />
        </aside>
      </div>

      {benchmark && <BenchmarkCard b={benchmark} />}
    </div>
  );
}

function SatisfiedRow({ s }: { s: Authorization["satisfied"][number] }) {
  return (
    <>
      <code>{s.claim_pattern}</code> ← <code title={s.record_id}>{shortHash(s.record_id, 8)}</code>
      <span title={s.kind}>
        <KindGlyph kind={s.kind} size={14} />
      </span>
      {s.producer && <ProducerTag producer={s.producer} />}
    </>
  );
}

function Section({ title, items, tone }: { title: string; items: { key: string; node: React.ReactNode }[]; tone: "good" | "bad" }) {
  return (
    <>
      <h4>
        {title} <span className="muted">({items.length})</span>
      </h4>
      {items.length === 0 ? (
        <div className="muted small">none</div>
      ) : (
        <ul className={`plain small list-${tone}`}>
          {items.map((it) => (
            <li key={it.key}>{it.node}</li>
          ))}
        </ul>
      )}
    </>
  );
}

function RecordBlock({ r, i, link, open, onToggle, flag }: { r: EvidenceRecord; i: number; link?: Bundle["links"][number]; open: boolean; onToggle: () => void; flag?: string }) {
  const cls = `block-${r.kind.toLowerCase().replace(/_/g, "-")}`;
  return (
    <li className={`chain-block ${cls} ${flag ? `flag-${flag}` : ""} ${open ? "open" : ""}`}>
      <button className="block-head" onClick={onToggle} aria-expanded={open}>
        <span className="idx">#{i}</span>
        <KindBadge kind={r.kind} />
        <ProducerTag producer={r.producer} />
        {flag && <span className={`flag flag-tag-${flag}`}>{flag}</span>}
        <span className="claim">{r.claim}</span>
      </button>
      <div className="block-meta small">
        <span>
          <span className="muted">procedure</span> <code>{r.procedure}</code>
        </span>
        <span>
          <span className="muted">subject</span> <code title={r.subject}>{shortHash(r.subject, 12)}</code>
        </span>
        <span>
          <span className="muted">id</span> <code title={r.id}>{shortHash(r.id, 12)}</code>
        </span>
        {link && (
          <span>
            <span className="muted">prev</span> <code title={link.prev_hash}>{shortHash(link.prev_hash, 12)}</code>
          </span>
        )}
        <span>
          <span className="muted">derived</span> {r.derived_at}
        </span>
      </div>
      {open && (
        <div className="block-body">
          <h5>result</h5>
          <pre className="json">{JSON.stringify(r.result, null, 2)}</pre>
          <h5>assumptions</h5>
          {r.assumptions.length ? (
            <ul className="plain small">
              {r.assumptions.map((a) => (
                <li key={a}>{a}</li>
              ))}
            </ul>
          ) : (
            <div className="muted small">none declared</div>
          )}
          <div className="small muted">
            inputs_hash {shortHash(r.inputs_hash, 20)}
            {link && <> · link hash {shortHash(link.hash, 20)}</>}
          </div>
        </div>
      )}
    </li>
  );
}

// ---------------------------------------------------------------- benchmark

const EXPLORE_METRICS: { key: string; label: string; digits: number; prefix?: string }[] = [
  { key: "n_nodes", label: "nodes", digits: 1 },
  { key: "n_failing", label: "failing", digits: 1 },
  { key: "failing_rate_per_100", label: "fail / 100", digits: 1 },
  { key: "first_decision_fail_index", label: "first decision FAIL", digits: 1, prefix: "#" },
  { key: "wall_ms", label: "wall ms", digits: 0 },
];
const MIN_METRICS: { key: string; label: string; digits: number }[] = [
  { key: "original_size", label: "original size", digits: 1 },
  { key: "minimal_size", label: "minimal size", digits: 1 },
  { key: "oracle_calls", label: "oracle calls", digits: 1 },
  { key: "wall_ms", label: "wall ms", digits: 0 },
];

function fmtAgg(a: Aggregate | undefined, digits: number, prefix = ""): React.ReactNode {
  if (!a) return "—";
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

function BenchmarkCard({ b }: { b: Benchmark }) {
  const strategies = Object.keys(b.exploration.aggregate);
  const def = (k: string) => b.definitions[k] ?? "";
  const minAgg = b.minimization.aggregate;
  const allVerified = minAgg.one_minimal_verified_all === true;
  return (
    <section className="panel bench">
      <div className="card-head">
        <h2>Benchmark</h2>
        <span className="small muted">
          <code>{b.scenario_id}</code> · seeds {b.seeds.join(", ")} · budget {b.budget} · {b.schedule_size} interventions per schedule · target <code>{b.target_invariant}</code>
        </span>
      </div>
      <div className="bench-grid">
        <div>
          <h4 title={def("aggregate")}>Exploration: guided vs random (mean / median over {b.seeds.length} seeds)</h4>
          <table className="table compare">
            <thead>
              <tr>
                <th>strategy</th>
                {EXPLORE_METRICS.map((m) => (
                  <th key={m.key} className="num" title={def(m.key)}>
                    {m.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {strategies.map((s) => (
                <tr key={s}>
                  <td>
                    <b>{s}</b>
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
        <div>
          <h4>Minimization (ddmin) over {b.minimization.cases.length} failing branches</h4>
          <table className="table compare">
            <thead>
              <tr>
                {MIN_METRICS.map((m) => (
                  <th key={m.key} className="num" title={def(m.key)}>
                    {m.label}
                  </th>
                ))}
                <th title={def("one_minimal_verified")}>1-minimal verified</th>
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
                  <span className={`pill ${allVerified ? "pill-pass" : "pill-fail"}`}>{allVerified ? `ALL ${b.minimization.cases.length}` : "NOT ALL"}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <p className="ceiling">
        <b>Claim ceiling:</b> {b.claim_ceiling}
      </p>
      <details className="details">
        <summary className="small">Definitions and environment</summary>
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
      </details>
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
