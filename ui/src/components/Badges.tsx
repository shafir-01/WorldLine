import type { EvidenceKind, Verdict } from "../types";

/**
 * Verdict glyph: shape AND colour (validated set, see styles.css). PASS = filled circle, FAIL = filled diamond,
 * UNKNOWN = hollow ring with a gap, so the three read apart in monochrome and under CVD.
 */
export function VerdictGlyph({ v, size = 12 }: { v: Verdict; size?: number }) {
  if (v === "FAIL")
    return (
      <svg width={size} height={size} viewBox="0 0 12 12" aria-hidden className="vglyph">
        <path d="M6 0.5L11.5 6 6 11.5 0.5 6z" fill="var(--fail)" />
      </svg>
    );
  if (v === "UNKNOWN")
    return (
      <svg width={size} height={size} viewBox="0 0 12 12" aria-hidden className="vglyph">
        <circle cx="6" cy="6" r="4.2" fill="none" stroke="var(--unknown)" strokeWidth="2.2" strokeDasharray="5 2.2" />
      </svg>
    );
  return (
    <svg width={size} height={size} viewBox="0 0 12 12" aria-hidden className="vglyph">
      <circle cx="6" cy="6" r="5" fill="var(--pass)" />
    </svg>
  );
}

export function VerdictPill({ v }: { v: Verdict }) {
  return (
    <span className={`pill pill-${v.toLowerCase()}`}>
      <VerdictGlyph v={v} size={10} />
      {v}
    </span>
  );
}

/**
 * Certainty badge: each EvidenceKind has a distinct SHAPE as well as a colour, so the ladder is readable
 * without colour vision and in monochrome print.
 *   PROVED_WITHIN_MODEL   square (closed, formal)
 *   EMPIRICALLY_VALIDATED circle (observed run)
 *   ATTESTED              shield (signed by the kernel)
 *   FAILED                diamond with X
 *   UNKNOWN               dashed circle with ?
 */
export function KindGlyph({ kind, size = 18 }: { kind: EvidenceKind; size?: number }) {
  const c = `var(--kind-${kind.toLowerCase().replace(/_/g, "-")})`;
  const s = size;
  const h = s / 2;
  switch (kind) {
    case "PROVED_WITHIN_MODEL":
      return (
        <svg width={s} height={s} viewBox="0 0 20 20" aria-hidden>
          <rect x="2.5" y="2.5" width="15" height="15" rx="1.5" fill={c} />
          <path d="M6 10.5l2.5 2.5L14 7.5" stroke="var(--on-kind)" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      );
    case "EMPIRICALLY_VALIDATED":
      return (
        <svg width={s} height={s} viewBox="0 0 20 20" aria-hidden>
          <circle cx="10" cy="10" r="7.5" fill={c} />
          <circle cx="10" cy="10" r="2.5" fill="var(--on-kind)" />
        </svg>
      );
    case "ATTESTED":
      return (
        <svg width={s} height={s} viewBox="0 0 20 20" aria-hidden>
          <path d="M10 2l7 2.5v5c0 4-3 7-7 8.5C6 16.5 3 13.5 3 9.5v-5z" fill={c} />
          <path d="M7 10l2 2 4-4.5" stroke="var(--on-kind)" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      );
    case "FAILED":
      return (
        <svg width={s} height={s} viewBox="0 0 20 20" aria-hidden>
          <path d="M10 1.5L18.5 10 10 18.5 1.5 10z" fill={c} />
          <path d="M7 7l6 6M13 7l-6 6" stroke="var(--on-kind)" strokeWidth="2" strokeLinecap="round" />
        </svg>
      );
    default:
      return (
        <svg width={s} height={s} viewBox="0 0 20 20" aria-hidden>
          <circle cx="10" cy="10" r="7.5" fill="none" stroke={c} strokeWidth="2" strokeDasharray="3 2.2" />
          <text x={h / (s / 20)} y="14" textAnchor="middle" fontSize="10" fontWeight="700" fill={c} fontFamily="inherit">
            ?
          </text>
        </svg>
      );
  }
}

export function KindBadge({ kind }: { kind: EvidenceKind }) {
  return (
    <span className={`kind kind-${kind.toLowerCase().replace(/_/g, "-")}`} title={KIND_HELP[kind]}>
      <KindGlyph kind={kind} size={16} />
      {KIND_LABEL[kind]}
    </span>
  );
}

export const KIND_LABEL: Record<EvidenceKind, string> = {
  PROVED_WITHIN_MODEL: "Proved in model",
  EMPIRICALLY_VALIDATED: "Observed",
  ATTESTED: "Attested",
  FAILED: "Failed",
  UNKNOWN: "Unknown",
};

export const KIND_HELP: Record<EvidenceKind, string> = {
  PROVED_WITHIN_MODEL: "Follows from the model and its assumptions (e.g. ddmin 1-minimality under determinism).",
  EMPIRICALLY_VALIDATED: "Observed on an actual deterministic run; recomputable from the trace.",
  ATTESTED: "Signed statement by the kernel (CI-held key). Agents may not emit this kind.",
  FAILED: "The claim was checked and does not hold.",
  UNKNOWN: "Could not be evaluated: a required signal is missing.",
};

export function ProducerTag({ producer }: { producer: string }) {
  const isKernel = producer === "kernel";
  return (
    <span className={`producer ${isKernel ? "producer-kernel" : "producer-agent"}`} title={isKernel ? "Emitted by the authorization kernel" : "Emitted by an agent; the kernel recomputes what it can"}>
      {isKernel ? "kernel" : producer.replace(/^agent:/, "agent · ")}
    </span>
  );
}
