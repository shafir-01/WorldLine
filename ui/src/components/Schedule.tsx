import type { FaultSchedule, Intervention } from "../types";
import { paramsText, selectorText } from "../format";

export function InterventionRow({ iv, highlight, muted }: { iv: Intervention; highlight?: boolean; muted?: boolean }) {
  const p = paramsText(iv);
  return (
    <li className={`iv ${highlight ? "iv-hl" : ""} ${muted ? "iv-muted" : ""}`}>
      <span className={`op op-${iv.op}`}>{iv.op}</span>
      <span className="sel">{selectorText(iv.target)}</span>
      {p && <span className="params">{p}</span>}
    </li>
  );
}

export function ScheduleList({ schedule, highlightIndex, mutedIndices, empty = "(empty schedule: baseline world)" }: { schedule: FaultSchedule; highlightIndex?: number; mutedIndices?: Set<number>; empty?: string }) {
  if (!schedule.length) return <div className="muted small">{empty}</div>;
  return (
    <ul className="schedule">
      {schedule.map((iv, i) => (
        <InterventionRow key={i} iv={iv} highlight={highlightIndex === i} muted={mutedIndices?.has(i)} />
      ))}
    </ul>
  );
}
