import { useEffect, useState } from "react";
import type { DataIndex, LoadedData, Trace, VerdictRecord } from "./types";

/**
 * All data is fetched relative to the page URL (`./data/...`), never from an absolute `/data` path,
 * so the built bundle works under a GitLab Pages subpath. In dev, vite.config.ts serves ui/fixtures at ./data.
 */
export function dataUrl(relPath: string): string {
  return new URL(`data/${relPath}`.replace(/\/+/g, "/"), document.baseURI).toString();
}

export async function fetchJson<T>(relPath: string): Promise<T> {
  const res = await fetch(dataUrl(relPath), { cache: "no-cache" });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText} while fetching ${relPath}`);
  return (await res.json()) as T;
}

export async function loadAll(): Promise<LoadedData> {
  const index = await fetchJson<DataIndex>("index.json");
  const f = index.files;
  const [exploration, minimal, genome, challenge, bundle, authorization] = await Promise.all([
    fetchJson<LoadedData["exploration"]>(f.exploration),
    fetchJson<LoadedData["minimal"]>(f.minimal),
    fetchJson<LoadedData["genome"]>(f.genome),
    fetchJson<LoadedData["challenge"]>(f.challenge),
    fetchJson<LoadedData["bundle"]>(f.bundle),
    fetchJson<LoadedData["authorization"]>(f.authorization),
  ]);
  return { index, exploration, minimal, genome, challenge, bundle, authorization };
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

const traceCache = new Map<string, Promise<Trace | null>>();

/** Traces exist only for branches listed in index.files.traces (root + failing branches by default). */
export function loadTrace(index: DataIndex, branchId: string): Promise<Trace | null> {
  const rel = index.files.traces[branchId];
  if (!rel) return Promise.resolve(null);
  let p = traceCache.get(branchId);
  if (!p) {
    p = fetchJson<Trace>(rel).catch(() => null);
    traceCache.set(branchId, p);
  }
  return p;
}

export function useTrace(index: DataIndex | undefined, branchId: string | null): { trace: Trace | null; loading: boolean; available: boolean } {
  const [trace, setTrace] = useState<Trace | null>(null);
  const [loading, setLoading] = useState(false);
  const available = !!(index && branchId && index.files.traces[branchId]);
  useEffect(() => {
    if (!index || !branchId) return;
    let alive = true;
    setLoading(true);
    loadTrace(index, branchId).then((t) => {
      if (!alive) return;
      setTrace(t);
      setLoading(false);
    });
    return () => {
      alive = false;
    };
  }, [index, branchId]);
  return { trace: branchId && trace && trace.branch_id === branchId ? trace : null, loading, available };
}

export function verdictsFor(data: LoadedData, branchId: string): VerdictRecord[] {
  return data.exploration.nodes.find((n) => n.branch_id === branchId)?.verdicts ?? [];
}
