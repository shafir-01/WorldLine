import { useEffect, useState } from "react";
import { useData, useTraceRegistry } from "./data";
import type { LoadedData } from "./types";
import { FOOTER_TEXT } from "./config";
import Story from "./views/Story";
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

  useEffect(() => {
    const onHash = () => setRoute(readHash());
    addEventListener("hashchange", onHash);
    return () => removeEventListener("hashchange", onHash);
  }, []);
  const go = (v: View, b: string | null = null) => {
    location.hash = `/${v}${b ? `/${encodeURIComponent(b)}` : ""}`;
    if (v !== "story") window.scrollTo(0, 0);
  };

  // Worlds opens on the failing branch ddmin started from; Timeline on the minimal counterexample.
  const defaultBranch = data.status === "ready" ? (view === "worlds" ? data.data.minimal.branch_id : data.data.minimal.minimal_branch_id) ?? "root" : "root";
  const selected = view === "story" ? defaultBranch : branch ?? defaultBranch;
  const select = (b: string) => go(view, b);

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-in">
          <a className="brand" href="#/" onClick={(e) => (e.preventDefault(), go("story"))} aria-label="WORLDLINE, back to the story">
            <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden>
              <circle cx="7" cy="16" r="3.5" fill="currentColor" />
              <path d="M10.5 16h6M16.5 16l8-8M16.5 16l8 8M16.5 16h9" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" />
              <path d="M26.5 21.5l3.2 3.2-3.2 3.2-3.2-3.2z" fill="var(--fail)" />
            </svg>
            <span className="brand-name">WORLDLINE</span>
          </a>
          <nav className="topnav" aria-label="Views">
            <button className={`tn ${view === "story" ? "active" : ""}`} onClick={() => go("story")} aria-current={view === "story" ? "page" : undefined}>
              Story
            </button>
            <span className="tn-sep" aria-hidden />
            <span className="tn-group-label">Inspect</span>
            {INSPECT.map((v) => (
              <button key={v.id} className={`tn ${view === v.id ? "active" : ""}`} onClick={() => go(v.id, v.id === "evidence" || view === "story" ? null : selected)} aria-current={view === v.id ? "page" : undefined}>
                {v.label}
              </button>
            ))}
          </nav>
          {data.status === "ready" && data.data.index.synthetic !== false && <span className="synthetic-tag" title="Every scenario in this viewer is synthetic">Synthetic data</span>}
        </div>
      </header>

      <main className={`main ${view === "story" ? "main-story" : "main-inspect"}`}>
        {data.status === "loading" && <div className="loading">Loading the run…</div>}
        {data.status === "error" && (
          <div className="panel empty error">
            <b>Could not load data.</b> {data.error}
            <p className="small">
              The viewer fetches <code>./data/*.json</code> relative to the page. Run <code>npm run copy-data</code> (or <code>npm run dev</code>, which serves <code>ui/fixtures</code>).
            </p>
          </div>
        )}
        {data.status === "ready" && <Ready data={data.data} view={view} selected={selected} select={select} go={go} />}
      </main>

      <footer className="footer" role="contentinfo">
        {FOOTER_TEXT}
      </footer>
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
