import { useEffect, useState } from "react";
import { useData, useTraceRegistry } from "./data";
import type { LoadedData } from "./types";
import Worlds from "./views/Worlds";
import Timeline from "./views/Timeline";
import Mechanism from "./views/Mechanism";
import Evidence from "./views/Evidence";

type View = "worlds" | "timeline" | "mechanism" | "evidence";
const VIEWS: { id: View; label: string; hint: string }[] = [
  { id: "worlds", label: "Worlds", hint: "guided vs random" },
  { id: "timeline", label: "Timeline", hint: "per-role lanes" },
  { id: "mechanism", label: "Mechanism", hint: "ddmin · genome · challenge" },
  { id: "evidence", label: "Evidence", hint: "bundle · authorization · benchmark" },
];

function readHash(): { view: View; branch: string | null } {
  const h = location.hash.replace(/^#\/?/, "");
  const [v, ...rest] = h.split("/");
  const view = VIEWS.some((x) => x.id === v) ? (v as View) : "worlds";
  const branch = rest.length ? decodeURIComponent(rest.join("/")) : null;
  return { view, branch };
}

export default function App() {
  const data = useData();
  const [{ view, branch }, setRoute] = useState(readHash);
  const selected = branch ?? "root";

  useEffect(() => {
    const onHash = () => setRoute(readHash());
    addEventListener("hashchange", onHash);
    return () => removeEventListener("hashchange", onHash);
  }, []);
  const go = (v: View, b: string | null = branch) => {
    location.hash = `/${v}${b ? `/${encodeURIComponent(b)}` : ""}`;
  };
  const select = (b: string) => go(view, b);

  // Default: select the minimal failing branch when data arrives and nothing is chosen.
  useEffect(() => {
    if (data.status === "ready" && !branch) {
      const d = data.data;
      const first = d.minimal.minimal_branch_id ?? Object.values(d.exploration.runs)[0]?.failing[0] ?? "root";
      go(view, first);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data.status]);

  return (
    <div className="app">
      <nav className="nav" aria-label="views">
        <div className="brand">
          <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden>
            <circle cx="8" cy="16" r="4" fill="var(--accent)" />
            <path d="M12 16h6M18 16l6-7M18 16l6 7" stroke="var(--accent)" strokeWidth="2.5" fill="none" />
            <circle cx="26" cy="8" r="3" fill="var(--pass)" />
            <circle cx="26" cy="24" r="3" fill="var(--fail)" />
          </svg>
          <div>
            <div className="brand-name">WORLDLINE</div>
            <div className="brand-sub">counterfactual viewer</div>
          </div>
        </div>
        {VIEWS.map((v) => (
          <button key={v.id} className={`nav-item ${view === v.id ? "active" : ""}`} onClick={() => go(v.id)} aria-current={view === v.id ? "page" : undefined}>
            <span className="nav-label">{v.label}</span>
            <span className="nav-hint">{v.hint}</span>
          </button>
        ))}
        <div className="nav-foot">
          {data.status === "ready" && (
            <>
              <div className="small muted">selected branch</div>
              <code className="block small">{selected}</code>
              <div className="small muted">
                scenario <b>{data.data.exploration.scenario_id}</b> · seed {data.data.exploration.seed}
              </div>
              {data.data.index.code_commit && (
                <div className="small muted" title={data.data.index.code_commit}>
                  commit <code>{data.data.index.code_commit.slice(0, 10)}</code>
                </div>
              )}
              {data.data.index.generated_at && <div className="small muted">generated {data.data.index.generated_at}</div>}
              {data.data.index.synthetic && <span className="pill pill-synthetic">SYNTHETIC DATA</span>}
            </>
          )}
        </div>
      </nav>

      <main className="main">
        {data.status === "loading" && <div className="panel empty">Loading ./data/index.json …</div>}
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

      <footer className="footer">
        <span>All scenarios synthetic.</span>
        <span>{data.status === "ready" && data.data.index.claim_ceiling ? data.data.index.claim_ceiling : "Transfer is within the shared Event v1 substrate, not across heterogeneous codebases."}</span>
        <span className="muted">Every FAIL has a witness; every UNKNOWN names its missing signal; every claim carries its EvidenceKind.</span>
      </footer>
    </div>
  );
}

function Ready({ data, view, selected, select, go }: { data: LoadedData; view: View; selected: string; select: (b: string) => void; go: (v: View, b?: string | null) => void }) {
  const registry = useTraceRegistry(data);
  return (
    <>
      {view === "worlds" && <Worlds data={data} registry={registry} selected={selected} onSelect={select} onOpenTimeline={(b) => go("timeline", b ?? selected)} />}
      {view === "timeline" && <Timeline data={data} registry={registry} selected={selected} onSelect={select} />}
      {view === "mechanism" && <Mechanism data={data} registry={registry} onOpenTimeline={(b) => go("timeline", b)} />}
      {view === "evidence" && <Evidence data={data} />}
    </>
  );
}
