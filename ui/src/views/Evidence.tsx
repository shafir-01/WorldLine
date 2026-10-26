import { useState } from "react";
import type { Authorization, AuthorizationFile, Bundle, EvidenceKind, EvidenceRecord } from "../types";
import { shortHash } from "../format";
import { KIND_HELP, KindBadge, KindGlyph, ProducerTag } from "../components/Badges";

const KINDS: EvidenceKind[] = ["PROVED_WITHIN_MODEL", "EMPIRICALLY_VALIDATED", "ATTESTED", "FAILED", "UNKNOWN"];

interface Props {
  bundle: Bundle;
  authorization: AuthorizationFile;
}

export default function Evidence({ bundle, authorization }: Props) {
  const [showDenied, setShowDenied] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const auth: Authorization = showDenied ? authorization.cases.denied : authorization.cases.authorized;
  const flagged = new Map<string, string>();
  for (const s of auth.stale) flagged.set(s.record_id, "stale");
  for (const d of auth.downgraded) flagged.set(d.record_id, "downgraded");
  for (const s of auth.satisfied) if (!flagged.has(s.record_id)) flagged.set(s.record_id, "satisfied");

  return (
    <div className="view">
      <header className="view-head">
        <div>
          <h1>Evidence</h1>
          <p className="lede">
            Every claim is typed by certainty and chained by hash. The kernel is the only producer allowed to emit <b>ATTESTED</b>; agent-produced results are recomputed where possible. Authorization reads only the bundle, the
            policy and the public key.
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
              {bundle.records.length} records
              {bundle.checkpoint && (
                <>
                  {" "}
                  · sealed {bundle.checkpoint.sealed_at} by <code>{bundle.checkpoint.signer}</code> ({bundle.checkpoint.algorithm})
                </>
              )}
            </span>
          </div>
          {bundle.verification && (
            <div className={`verify ${bundle.verification.ok ? "ok" : "nok"}`}>
              {bundle.verification.ok ? "chain verified" : "chain verification FAILED"} · checked {bundle.verification.checked_at}
              {bundle.verification.findings.length > 0 && <ul>{bundle.verification.findings.map((f) => <li key={f}>{f}</li>)}</ul>}
            </div>
          )}
          <ol className="chain">
            <li className="chain-genesis">
              <span className="hash">genesis</span>
            </li>
            {bundle.records.map((r, i) => (
              <RecordBlock key={r.id} r={r} i={i} open={open === r.id} onToggle={() => setOpen(open === r.id ? null : r.id)} flag={flagged.get(r.id)} />
            ))}
            {bundle.checkpoint && (
              <li className="chain-checkpoint">
                <div className="block-head">
                  <KindGlyph kind="ATTESTED" size={16} /> <b>checkpoint</b> <span className="hash" title={bundle.checkpoint.head}>head {shortHash(bundle.checkpoint.head, 16)}</span>
                </div>
                <div className="small muted">
                  signature {bundle.checkpoint.signature.slice(0, 28)}… · key {bundle.checkpoint.public_key.slice(0, 20)}…
                </div>
              </li>
            )}
          </ol>
        </section>

        <aside className="panel auth-panel">
          <div className="card-head">
            <h2>Authorization</h2>
            <label className="toggle">
              <input type="checkbox" checked={showDenied} onChange={(e) => setShowDenied(e.target.checked)} />
              show DENIED case
            </label>
          </div>
          <div className={`decision decision-${auth.decision.toLowerCase()}`}>
            <div className="decision-word">{auth.decision}</div>
            <div className="small">
              artifact <code title={auth.artifact_digest}>{shortHash(auth.artifact_digest, 16)}</code>
              {auth.evaluated_at && <> · evaluated {auth.evaluated_at}</>}
            </div>
          </div>
          <h4>Reasons</h4>
          <ul className="reasons">
            {auth.reasons.map((r) => (
              <li key={r}>
                <code className={auth.decision === "DENIED" ? "bad" : ""}>{r}</code>
                {auth.reason_details?.[r] && <div className="small muted">{auth.reason_details[r]}</div>}
              </li>
            ))}
          </ul>
          {auth.policy && (
            <>
              <h4>Policy</h4>
              <ul className="plain small">
                {auth.policy.required.map((p) => (
                  <li key={p.claim_pattern}>
                    <code>{p.claim_pattern}</code> needs ≥ <KindBadge kind={p.min_kind} />
                  </li>
                ))}
                {Object.entries(auth.policy.forbid_producers_for_kinds).map(([k, ps]) => (
                  <li key={k}>
                    <code>{k}</code> forbidden from {ps.join(", ")}
                  </li>
                ))}
                <li>max age {auth.policy.max_age_s}s</li>
              </ul>
            </>
          )}
          <Section title="Satisfied" items={auth.satisfied.map((s) => `${s.claim_pattern} ← ${shortHash(s.record_id, 10)} (${s.kind})`)} tone="good" />
          <Section title="Missing" items={auth.missing.map((m) => `${m.claim_pattern} needs ${m.min_kind}${m.note ? ` — ${m.note}` : ""}`)} tone="bad" />
          <Section title="Stale" items={auth.stale.map((s) => `${s.claim} (${shortHash(s.record_id, 10)}) age ${s.age_s}s > ${s.max_age_s}s`)} tone="bad" />
          <Section title="Downgraded" items={auth.downgraded.map((d) => `${shortHash(d.record_id, 10)}: ${d.from} → ${d.to} — ${d.reason}`)} tone="bad" />
        </aside>
      </div>
    </div>
  );
}

function Section({ title, items, tone }: { title: string; items: string[]; tone: "good" | "bad" }) {
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
            <li key={it}>{it}</li>
          ))}
        </ul>
      )}
    </>
  );
}

function RecordBlock({ r, i, open, onToggle, flag }: { r: EvidenceRecord; i: number; open: boolean; onToggle: () => void; flag?: string }) {
  const cls = `block-${r.kind.toLowerCase().replace(/_/g, "-")}`;
  return (
    <li className={`chain-block ${cls} ${flag ? `flag-${flag}` : ""} ${open ? "open" : ""}`}>
      <button className="block-head" onClick={onToggle} aria-expanded={open}>
        <span className="idx">#{i + 1}</span>
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
        {r.prev && (
          <span>
            <span className="muted">prev</span> <code title={r.prev}>{shortHash(r.prev, 12)}</code>
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
          <div className="small muted">inputs_hash {shortHash(r.inputs_hash, 20)}</div>
        </div>
      )}
    </li>
  );
}
