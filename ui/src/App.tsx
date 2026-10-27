import { useEffect, useRef, useState } from "react";
import { useData, useTraceRegistry } from "./data";
import type { LoadedData } from "./types";
import { FOOTER_TEXT, STORY_BEATS, VERIFIER_DOCS_URL, REPO_URL } from "./config";
import Story from "./views/Story";
import { StorySkeleton, jumpToBeat } from "./views/Landing";
import Worlds from "./views/Worlds";
import Timeline from "./views/Timeline";
import Mechanism from "./views/Mechanism";
import Evidence from "./views/Evidence";

type View = "story" | "worlds" | "timeline" | "mechanism" | "evidence";
const INSPECT: { id: Exclude<View, "story">; label: string }[] = [
  { id: "worlds", label: "Worlds" },
  { id: "timeline", label: "Timeline" },
  { id: "mechanism", label: "Mechanism" },
  { id: "evidence", label: "Evidence" },
];

function readHash(): { view: View; branch: string | null } {
  const h = location.hash.replace(/^#\/?/, "");
  const [v, ...rest] = h.split("/");
  const view: View = INSPECT.some((x) => x.id === v) ? (v as View) : "story";
  const branch = rest.length ? decodeURIComponent(rest.join("/")) : null;
  return { view, branch };
}

export default function App() {
  const data = useData();
  const [{ view, branch }, setRoute] = useState(readHash);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    const onHash = () => setRoute(readHash());
    addEventListener("hashchange", onHash);
    return () => removeEventListener("hashchange", onHash);
  }, []);
  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMenuOpen(false);
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [menuOpen]);

  const go = (v: View, b: string | null = null) => {
    setMenuOpen(false);
    location.hash = `/${v}${b ? `/${encodeURIComponent(b)}` : ""}`;
    window.scrollTo(0, 0);
  };

  // Worlds opens on the failing branch ddmin started from; Timeline on the minimal counterexample.
  const defaultBranch = data.status === "ready" ? (view === "worlds" ? data.data.minimal.branch_id : data.data.minimal.minimal_branch_id) ?? "root" : "root";
  const selected = view === "story" ? defaultBranch : branch ?? defaultBranch;
  const select = (b: string) => go(view, b);
  const ready = data.status === "ready" ? data.data : null;
  const simulated = ready ? ready.index.synthetic !== false : true;

  const navItems = (
    <>
      <button type="button" className={`tn ${view === "story" ? "active" : ""}`} onClick={() => go("story")} aria-current={view === "story" ? "page" : undefined}>
        Story
      </button>
      <span className="tn-sep" aria-hidden />
      <span className="tn-group-label">
        Inspect
      </span>
      {INSPECT.map((v) => (
        <button type="button" key={v.id} className={`tn ${view === v.id ? "active" : ""}`} onClick={() => go(v.id, v.id === "evidence" || view === "story" ? null : selected)} aria-current={view === v.id ? "page" : undefined}>
          {v.label}
        </button>
      ))}
    </>
  );

  return (
    <div className="app">
      <a
        className="skip-link"
        href="#main"
        onClick={(e) => {
          // The app routes on the hash, so following href="#main" would leave the current view.
          e.preventDefault();
          const m = document.getElementById("main");
          if (m) {
            m.focus();
            m.scrollIntoView();
          }
        }}
      >
        Skip to content
      </a>
      <header className="topbar">
        <div className="topbar-in">
          <a className="brand" href="#/" onClick={(e) => (e.preventDefault(), go("story"))} aria-label="WORLDLINE home">
            <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden>
              <circle cx="7" cy="16" r="3.5" fill="currentColor" />
              <path d="M10.5 16h6M16.5 16l8-8M16.5 16l8 8M16.5 16h9" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" />
              <path d="M26.5 21.5l3.2 3.2-3.2 3.2-3.2-3.2z" fill="var(--fail)" />
            </svg>
            <span className="brand-name">WORLDLINE</span>
          </a>
          <nav className="topnav" aria-label="Views">
            {navItems}
          </nav>
          <div className="topbar-right">
            {simulated && <SimulatedBadge data={ready} />}
            <ThemeToggle />
            <a className="api-link" href={REPO_URL} target="_blank" rel="noreferrer">
              GitLab
              <ExtIcon />
            </a>
            <a className="api-link" href={VERIFIER_DOCS_URL} target="_blank" rel="noreferrer">
              Open verifier API
              <ExtIcon />
            </a>
            <button type="button" className="menu-btn" aria-expanded={menuOpen} aria-controls="mobile-menu" aria-label={menuOpen ? "Close menu" : "Open menu"} onClick={() => setMenuOpen(!menuOpen)}>
              <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden>
                {menuOpen ? <path d="M4 4l10 10M14 4L4 14" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" /> : <path d="M3 6h12M3 12h12" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />}
              </svg>
            </button>
          </div>
        </div>
        {menuOpen && (
          <div className="mobile-menu" id="mobile-menu">
            <nav aria-label="Views" className="mobile-nav">
              {navItems}
            </nav>
            <a className="mobile-api" href={VERIFIER_DOCS_URL} target="_blank" rel="noreferrer">
              Open verifier API <ExtIcon />
            </a>
            <ThemeToggle />
          </div>
        )}
        {view === "story" && <StoryProgress ready={!!ready} />}
      </header>

      <main id="main" tabIndex={-1} className={`main ${view === "story" ? "main-story" : "main-inspect"}`}>
        {data.status === "loading" && (view === "story" ? <StorySkeleton /> : <InspectSkeleton />)}
        {data.status === "error" && <LoadError message={data.error} />}
        {ready && <Ready data={ready} view={view} selected={selected} select={select} go={go} />}
      </main>

      <footer className="footer" role="contentinfo">
        {FOOTER_TEXT}
      </footer>
    </div>
  );
}

type Theme = "light" | "dark" | "system";
function readTheme(): Theme {
  try { const t = localStorage.getItem("worldline:theme"); if (t === "light" || t === "dark") return t; } catch { /* no storage */ }
  return "system";
}
function applyTheme(t: Theme) {
  const root = document.documentElement;
  if (t === "system") root.removeAttribute("data-theme"); else root.setAttribute("data-theme", t);
  try { if (t === "system") localStorage.removeItem("worldline:theme"); else localStorage.setItem("worldline:theme", t); } catch { /* no storage */ }
}
function useTheme(): [Theme, (t: Theme) => void] {
  const [t, setT] = useState<Theme>(() => readTheme());
  useEffect(() => { applyTheme(t); }, [t]);
  return [t, setT];
}
function ThemeToggle() {
  const [t, setT] = useTheme();
  const isDark = t === "dark" || (t === "system" && typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  const next: Theme = isDark ? "light" : "dark";
  return (
    <button type="button" className="theme-btn" onClick={() => setT(next)} aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"} aria-pressed={isDark}>
      <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden>
        {isDark ? <circle cx="8" cy="8" r="4" fill="currentColor" /> : <path d="M11 2.5A6 6 0 1 0 13.5 11 5 5 0 0 1 11 2.5z" fill="currentColor" />}
      </svg>
      <span className="theme-label">{isDark ? "Light" : "Dark"}</span>
    </button>
  );
}

function ExtIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden>
      <path d="M6 3H3.5A.5.5 0 0 0 3 3.5v9a.5.5 0 0 0 .5.5h9a.5.5 0 0 0 .5-.5V10M9 3h4v4M13 3L7.5 8.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** Neutral badge: what is simulated and what is measured. Tooltip opens on hover and keyboard focus. */
function SimulatedBadge({ data }: { data: LoadedData | null }) {
  const ex = data?.exploration;
  return (
    <span className="sim">
      <button type="button" className="sim-badge" aria-describedby="sim-tip">
        <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden>
          <circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
          <path d="M8 7.2v4M8 4.8v.1" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
        Simulated scenario
      </button>
      <span role="tooltip" id="sim-tip" className="sim-tip">
        <b>Simulated:</b> the world itself. {ex ? <>The {ex.scenario_id.replace(/_/g, " ")} scenario (seed {ex.seed})</> : <>The scenario</>}, its actors, messages and timing faults are synthetic.
        <br />
        <b>Measured:</b> everything you see about it. Every count, timing, verdict and signature on this page is computed by the pipeline from that simulation and can be re-checked.
      </span>
    </span>
  );
}

/** Seven-segment progress for the story: which beat is on screen, how far through it, click to jump. */
function StoryProgress({ ready }: { ready: boolean }) {
  const [state, setState] = useState<{ active: number; frac: number }>({ active: 0, frac: 0 });
  const raf = useRef(0);
  useEffect(() => {
    if (!ready) return;
    const measure = () => {
      raf.current = 0;
      const line = 120 + window.innerHeight * 0.25;
      let active = 0;
      let frac = 0;
      for (let i = 1; i <= STORY_BEATS.length; i++) {
        const el = document.getElementById(`beat-${i}`);
        if (!el) continue;
        const r = el.getBoundingClientRect();
        if (r.top <= line) {
          active = i;
          frac = Math.max(0, Math.min(1, (line - r.top) / Math.max(1, r.height)));
        }
      }
      // At the very bottom the last beat is fully read.
      if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4 && active > 0) {
        active = STORY_BEATS.length;
        frac = 1;
      }
      setState((s) => (s.active === active && Math.abs(s.frac - frac) < 0.01 ? s : { active, frac }));
    };
    const onScroll = () => {
      if (!raf.current) raf.current = requestAnimationFrame(measure);
    };
    measure();
    const t = setTimeout(measure, 600);
    addEventListener("scroll", onScroll, { passive: true });
    addEventListener("resize", onScroll);
    return () => {
      clearTimeout(t);
      removeEventListener("scroll", onScroll);
      removeEventListener("resize", onScroll);
      if (raf.current) cancelAnimationFrame(raf.current);
    };
  }, [ready]);
  const { active, frac } = state;
  return (
    <div className="progress">
      <div className="progress-in">
        <span className="progress-now" aria-live="polite">
          {active === 0 ? (
            <>
              <span className="progress-count">7 steps</span>
              <span className="progress-title">Overview</span>
            </>
          ) : (
            <>
              <span className="progress-count">
                {active}/{STORY_BEATS.length}
              </span>
              <span className="progress-title">{STORY_BEATS[active - 1].short}</span>
            </>
          )}
        </span>
        <ol className="progress-steps" aria-label="Story progress">
          {STORY_BEATS.map((b, i) => {
            const n = i + 1;
            const fill = n < active ? 1 : n === active ? Math.max(0.08, frac) : 0;
            return (
              <li key={b.short}>
                <button type="button" className={`ps ${n === active ? "ps-active" : ""} ${n < active ? "ps-done" : ""}`} onClick={() => jumpToBeat(n)} aria-label={`${b.short}, step ${n} of ${STORY_BEATS.length}: ${b.title}`} aria-current={n === active ? "step" : undefined} disabled={!ready}>
                  <span className="ps-label">{b.short}</span>
                  <span className="ps-track">
                    <span className="ps-fill" style={{ transform: `scaleX(${fill})` }} />
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
      </div>
    </div>
  );
}

function InspectSkeleton() {
  return (
    <div className="view" aria-busy="true" aria-label="Loading the run">
      <span className="sk sk-title" style={{ width: 280, margin: 0 }} />
      <span className="sk sk-line" style={{ width: "min(560px, 90%)", margin: 0 }} />
      <div className="sk sk-card" style={{ height: 460 }} />
    </div>
  );
}

function LoadError({ message }: { message: string }) {
  return (
    <div className="state-wrap">
      <div className="state-card" role="alert">
        <span className="state-icon state-icon-warn" aria-hidden>
          <svg width="22" height="22" viewBox="0 0 24 24">
            <path d="M12 3.5l9.5 16.5h-19z" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
            <path d="M12 10v4.5M12 17.2v.1" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" />
          </svg>
        </span>
        <h2>The run's data didn't load</h2>
        <p>
          The viewer reads <code>./data/*.json</code> next to this page. Build with <code>npm run build</code> (it copies the data first) or start <code>npm run dev</code>, which serves the fixtures.
        </p>
        <p className="state-detail">{message}</p>
        <button type="button" className="btn btn-primary" onClick={() => location.reload()}>
          Try again
        </button>
      </div>
    </div>
  );
}

function Ready({ data, view, selected, select, go }: { data: LoadedData; view: View; selected: string; select: (b: string) => void; go: (v: View, b?: string | null) => void }) {
  const registry = useTraceRegistry(data);
  return (
    <>
      {view === "story" && <Story data={data} registry={registry} onInspect={(v, b) => go(v, b ?? null)} />}
      {view === "worlds" && <Worlds data={data} registry={registry} selected={selected} onSelect={select} onOpenTimeline={(b) => go("timeline", b ?? selected)} />}
      {view === "timeline" && <Timeline data={data} registry={registry} selected={selected} onSelect={select} />}
      {view === "mechanism" && <Mechanism data={data} registry={registry} onOpenTimeline={(b) => go("timeline", b)} />}
      {view === "evidence" && <Evidence data={data} />}
    </>
  );
}
