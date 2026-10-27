import type { LoadedData } from "../types";

/** Scroll to a story beat and record it in the URL without re-routing (#/story/<n>). */
export function jumpToBeat(n: number) {
  const el = document.getElementById(`beat-${n}`);
  if (!el) return;
  const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  history.replaceState(null, "", `#/story/${n}`);
}

interface HeroProps {
  data: LoadedData;
  totalWorlds: number;
  totalFailing: number;
}

export default function Landing({ data, totalWorlds, totalFailing }: HeroProps) {
  const m = data.minimal;
  const auth = data.authorization;
  const authorized = auth.decision === "AUTHORIZED";
  const nRecords = data.bundle.records.length;
  return (
    <section className="hero" aria-labelledby="hero-h">
      <div className="hero-glow" aria-hidden />
      <div className="hero-in">
        <p className="hero-kicker reveal" style={{ "--i": 0 } as React.CSSProperties}>
          Release testing for time-sensitive decisions
        </p>
        <h1 id="hero-h" className="reveal" style={{ "--i": 1 } as React.CSSProperties}>
          Catch decisions made on late information, before they ship.
        </h1>
        <p className="hero-sub reveal" style={{ "--i": 2 } as React.CSSProperties}>
          WORLDLINE replays your system with messages arriving at different times, pins every wrong call on a single cause, and holds the release until signed evidence clears it.
        </p>
        <div className="hero-actions reveal" style={{ "--i": 3 } as React.CSSProperties}>
          <button type="button" className="btn btn-primary btn-lg" onClick={() => jumpToBeat(1)}>
            See it work
            <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden>
              <path d="M8 3v9.5M4 8.5l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
          <button type="button" className="btn btn-secondary btn-lg" onClick={() => jumpToBeat(7)}>
            Verify a release
            <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden>
              <path d="M3 8h9M8.5 4.5L12 8l-3.5 3.5" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        </div>
        <dl className="stat-tiles reveal" style={{ "--i": 4 } as React.CSSProperties} aria-label="This run in four numbers">
          <div className="tile">
            <dt>Replayed</dt>
            <dd>
              <span className="tile-num">
                <span className="tile-v">{totalWorlds}</span>
                <span className="tile-u">worlds</span>
              </span>
              <span className="tile-note">same hour, shifted message timing</span>
            </dd>
          </div>
          <div className="tile">
            <dt>Wrong outcome</dt>
            <dd>
              <span className="tile-num">
                <span className="tile-v">{totalFailing}</span>
                <span className="tile-u">worlds</span>
              </span>
              <span className="tile-note">broke a safety rule</span>
            </dd>
          </div>
          <div className="tile">
            <dt>Root cause</dt>
            <dd>
              <span className="tile-num">
                <span className="tile-v">{m.minimal.length}</span>
                <span className="tile-u">{m.minimal.length === 1 ? "change" : "changes"}</span>
              </span>
              <span className="tile-note">
                narrowed from {m.original.length}
                {m.one_minimal_verified ? ", verified minimal" : ""}
              </span>
            </dd>
          </div>
          <div className="tile">
            <dt>Release gate</dt>
            <dd>
              <span className="tile-num">
                <span className="tile-v">{nRecords}</span>
                <span className="tile-u">records</span>
              </span>
              <span className="tile-note tile-gate">
                <GateGlyph ok={authorized} />
                {authorized ? "signed, release authorized" : "signed, release denied"}
              </span>
            </dd>
          </div>
        </dl>
      </div>
    </section>
  );
}

function GateGlyph({ ok }: { ok: boolean }) {
  return ok ? (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden>
      <circle cx="6" cy="6" r="5.5" fill="var(--pass)" />
      <path d="M3.4 6.2l1.7 1.7 3.4-3.6" fill="none" stroke="#fff" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ) : (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden>
      <path d="M6 0.5L11.5 6 6 11.5 0.5 6z" fill="var(--fail)" />
    </svg>
  );
}

/** Placeholder with the hero's and first beat's footprint, shown while the run loads (no layout shift). */
export function StorySkeleton() {
  return (
    <div className="story" aria-busy="true" aria-label="Loading the run">
      <section className="hero">
        <div className="hero-in">
          <span className="sk sk-line" style={{ width: 220, margin: "0 auto 22px" }} />
          <span className="sk sk-title" style={{ width: "min(780px, 92%)" }} />
          <span className="sk sk-title" style={{ width: "min(560px, 70%)" }} />
          <span className="sk sk-line" style={{ width: "min(620px, 90%)", margin: "26px auto 8px" }} />
          <span className="sk sk-line" style={{ width: "min(480px, 70%)", margin: "0 auto 34px" }} />
          <div className="hero-actions">
            <span className="sk sk-btn" />
            <span className="sk sk-btn" />
          </div>
          <div className="stat-tiles">
            {[0, 1, 2, 3].map((i) => (
              <div className="tile" key={i}>
                <span className="sk sk-line" style={{ width: 80 }} />
                <span className="sk sk-num" />
                <span className="sk sk-line" style={{ width: "70%" }} />
              </div>
            ))}
          </div>
        </div>
      </section>
      <section className="beat">
        <div className="beat-in">
          <div className="sk sk-card" style={{ height: 420 }} />
        </div>
      </section>
    </div>
  );
}
