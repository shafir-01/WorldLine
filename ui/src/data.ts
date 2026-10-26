import { useEffect, useMemo, useState } from "react";
import type { Authorization, Benchmark, BranchNode, DataIndex, FaultSchedule, LoadedData, Trace, VerdictRecord } from "./types";

/**
 * All data is fetched relative to the page URL (`./data/...`), never from an absolute `/data` path,
 * so the built bundle works under a GitLab Pages subpath. In dev, vite.config.ts serves ui/fixtures at ./data.
 *
 * File names are fixed by the pipeline (`worldline report` writes index.json as a manifest of path -> digest,
 * not a logical map), so the loader hardcodes them: see ui/README.md "Data contract".
 */
export const FILES = {
  index: "index.json",
  exploration: "exploration.json",
  minimal: "minimal_schedule.json",
  genome: "genome.json",
  challenge: "challenge.json",
  bundle: "bundle.json",
  authorization: "authorization.json",
  authorizationDenied: "authorization_denied.json",
  benchmark: "benchmark.json",
} as const;

export function dataUrl(relPath: string): string {
  return new URL(`data/${relPath}`.replace(/\\/g, "/").replace(/\/+/g, "/"), document.baseURI).toString();
}

export async function fetchJson<T>(relPath: string): Promise<T> {
  const res = await fetch(dataUrl(relPath), { cache: "no-cache" });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} while fetching ${relPath}`);
  return (await res.json()) as T;
}

async function optional<T>(relPath: string): Promise<T | null> {
  try {
    return await fetchJson<T>(relPath);
  } catch {
    return null;
  }
}

export async function loadAll(): Promise<LoadedData> {
  const index = await fetchJson<DataIndex>(FILES.index);
  const [exploration, minimal, genome, challenge, bundle, authorization, authorizationDenied, benchmark] = await Promise.all([
    fetchJson<LoadedData["exploration"]>(FILES.exploration),
    fetchJson<LoadedData["minimal"]>(FILES.minimal),
    fetchJson<LoadedData["genome"]>(FILES.genome),
    fetchJson<LoadedData["challenge"]>(FILES.challenge),
    fetchJson<LoadedData["bundle"]>(FILES.bundle),
    fetchJson<Authorization>(FILES.authorization),
    optional<Authorization>(FILES.authorizationDenied),
    optional<Benchmark>(FILES.benchmark),
  ]);
  return { index, exploration, minimal, genome, challenge, bundle, authorization, authorizationDenied, benchmark };
}

export type DataState = { status: "loading" } | { status: "error"; error: string } | { status: "ready"; data: LoadedData };

export function useData(): DataState {
  const [state, setState] = useState<DataState>({ status: "loading" });
  useEffect(() => {
    let alive = true;
    loadAll()
      .then((data) => alive && setState({ status: "ready", data }))
      .catch((e: unknown) => alive && setState({ status: "error", error: e instanceof Error ? e.message : String(e) }));
    return () => {
      alive = false;
    };
  }, []);
  return state;
}

// ---------------------------------------------------------------- trace registry

export type TraceGroup = "root" | "minimal" | "challenge" | "guided" | "random" | "other";

/** Everything the viewer knows about one openable trace, resolved from exploration.trace_files, minimal_schedule.trace_files and challenge.primary.trace_file. */
export interface TraceEntry {
  /** Routing key: the branch_id for exploration/minimal traces, `challenge:<scenario_id>` for challenge counterexamples. */
  key: string;
  label: string;
  group: TraceGroup;
  traceFile: string;
  verdictsFile?: string;
  /** Verdicts embedded in the source file (exploration nodes, challenge result); otherwise fetched from verdictsFile. */
  inlineVerdicts?: VerdictRecord[];
  schedule?: FaultSchedule;
  failingInvariants?: string[];
  scenarioId: string;
}

export const CHALLENGE_PREFIX = "challenge:";

export function challengeKey(scenarioId: string): string {
  return `${CHALLENGE_PREFIX}${scenarioId}`;
}

export function allNodes(data: LoadedData): BranchNode[] {
  return Object.values(data.exploration.runs).flatMap((r) => r.nodes);
}

export function findNode(data: LoadedData, branchId: string): BranchNode | undefined {
  for (const run of Object.values(data.exploration.runs)) {
    const n = run.nodes.find((x) => x.branch_id === branchId);
    if (n) return n;
  }
  return undefined;
}

export function buildTraceRegistry(data: LoadedData): Map<string, TraceEntry> {
  const reg = new Map<string, TraceEntry>();
  const ex = data.exploration;
  for (const [branchId, ref] of Object.entries(ex.trace_files)) {
    const node = findNode(data, branchId);
    const group: TraceGroup = branchId === "root" ? "root" : ref.strategy === "guided" || ref.strategy === "random" ? ref.strategy : "other";
    reg.set(branchId, {
      key: branchId,
      label: branchId === "root" ? "root (baseline)" : branchId,
      group,
      traceFile: ref.trace,
      verdictsFile: ref.verdicts,
      inlineVerdicts: node?.verdicts,
      schedule: node?.schedule,
      failingInvariants: ref.failing_invariants ?? node?.failing_invariants,
      scenarioId: ex.scenario_id,
    });
  }
  const m = data.minimal;
  if (m.trace_files?.minimal) {
    reg.set(m.minimal_branch_id, {
      key: m.minimal_branch_id,
      label: `${m.minimal_branch_id} (minimal)`,
      group: "minimal",
      traceFile: m.trace_files.minimal,
      verdictsFile: m.trace_files.minimal_verdicts,
      inlineVerdicts: reg.get(m.minimal_branch_id)?.inlineVerdicts,
      schedule: m.minimal,
      failingInvariants: [m.target_invariant],
      scenarioId: m.scenario_id,
    });
  }
  const results = [data.challenge.primary, ...data.challenge.controls];
  for (const c of results) {
    if (!c.trace_file) continue;
    reg.set(challengeKey(c.scenario_id), {
      key: challengeKey(c.scenario_id),
      label: `challenge counterexample: ${c.scenario_id}`,
      group: "challenge",
      traceFile: c.trace_file,
      inlineVerdicts: c.verdicts,
      schedule: c.instantiated_schedule,
      failingInvariants: c.verdicts.filter((v) => v.result === "FAIL").map((v) => v.invariant_id),
      scenarioId: c.scenario_id,
    });
  }
  return reg;
}

export function useTraceRegistry(data: LoadedData): Map<string, TraceEntry> {
  return useMemo(() => buildTraceRegistry(data), [data]);
}

const traceCache = new Map<string, Promise<Trace | null>>();
const verdictCache = new Map<string, Promise<VerdictRecord[] | null>>();

function cached<T>(cache: Map<string, Promise<T | null>>, file: string): Promise<T | null> {
  let p = cache.get(file);
  if (!p) {
    p = fetchJson<T>(file).catch(() => null);
    cache.set(file, p);
  }
  return p;
}

export interface TraceState {
  trace: Trace | null;
  verdicts: VerdictRecord[];
  loading: boolean;
  available: boolean;
  entry: TraceEntry | null;
}

/** Loads the trace (and its verdict file, if the verdicts are not already inline) for one registry key. */
export function useTrace(registry: Map<string, TraceEntry>, key: string | null): TraceState {
  const entry = key ? registry.get(key) ?? null : null;
  const [state, setState] = useState<{ key: string; trace: Trace | null; verdicts: VerdictRecord[] } | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!entry) return;
    let alive = true;
    setLoading(true);
    const vp = entry.inlineVerdicts ? Promise.resolve(entry.inlineVerdicts) : entry.verdictsFile ? cached<VerdictRecord[]>(verdictCache, entry.verdictsFile) : Promise.resolve(null);
    Promise.all([cached<Trace>(traceCache, entry.traceFile), vp]).then(([trace, verdicts]) => {
      if (!alive) return;
      setState({ key: entry.key, trace, verdicts: verdicts ?? [] });
      setLoading(false);
    });
    return () => {
      alive = false;
    };
  }, [entry]);
  const ready = state && key && state.key === key ? state : null;
  return { trace: ready?.trace ?? null, verdicts: ready?.verdicts ?? [], loading: !!entry && (loading || !ready), available: !!entry, entry };
}
