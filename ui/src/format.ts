import type { BranchNode, Intervention, Selector, Verdict } from "./types";

export const NS = 1_000_000_000;

export function secs(ns: number): string {
  const s = ns / NS;
  if (Math.abs(s) >= 60 && Number.isInteger(s / 60)) return `${s / 60}m`;
  return `${Number.isInteger(s) ? s : s.toFixed(1)}s`;
}

export function tClock(ns: number): string {
  const s = Math.round(ns / NS);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `t+${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}`;
}

export function shortHash(h: string, n = 10): string {
  const body = h.replace(/^sha256:/, "");
  return body.length > n ? `${body.slice(0, n)}…` : body;
}

export function shortBranch(id: string): string {
  if (id === "root") return "root";
  const [, h] = id.split("/");
  return `root/${(h ?? "").slice(0, 8)}`;
}

export function selectorText(t: Selector): string {
  if (t.event_id) return `event ${t.event_id}`;
  const parts: string[] = [];
  if (t.kind) parts.push(`kind=${t.kind}`);
  if (t.role) parts.push(`role=${t.role}`);
  if (t.nth != null) parts.push(`nth=${t.nth}`);
  for (const [k, v] of Object.entries(t)) if (!["event_id", "kind", "role", "nth"].includes(k)) parts.push(`${k}=${String(v)}`);
  return parts.join(" ") || "(any)";
}

export function paramsText(iv: Intervention): string {
  const p = iv.params ?? {};
  return Object.entries(p)
    .map(([k, v]) => {
      if (typeof v === "number" && k.endsWith("_ns")) return `${k.replace(/_ns$/, "")}=${secs(v)}`;
      return `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`;
    })
    .join(", ");
}

export function interventionText(iv: Intervention): string {
  const p = paramsText(iv);
  return `${iv.op} ${selectorText(iv.target)}${p ? ` (${p})` : ""}`;
}

export function nodeStatus(n: BranchNode): Verdict {
  if (n.verdicts.some((v) => v.result === "FAIL")) return "FAIL";
  if (n.verdicts.some((v) => v.result === "UNKNOWN")) return "UNKNOWN";
  return "PASS";
}

export function cmp<T>(a: T, b: T): number {
  return a < b ? -1 : a > b ? 1 : 0;
}
