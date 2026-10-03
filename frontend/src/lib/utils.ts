import type { Metrics } from "./types";

/** green > 0.8, yellow 0.6-0.8, red < 0.6 */
export function scoreTone(score: number | null | undefined) {
  if (score == null) return { text: "text-slate-500", bg: "bg-slate-100", bar: "bg-slate-400", hex: "#94a3b8" };
  if (score > 0.8) return { text: "text-emerald-700", bg: "bg-emerald-100", bar: "bg-emerald-500", hex: "#10b981" };
  if (score >= 0.6) return { text: "text-amber-700", bg: "bg-amber-100", bar: "bg-amber-500", hex: "#f59e0b" };
  return { text: "text-red-700", bg: "bg-red-100", bar: "bg-red-500", hex: "#ef4444" };
}

export const fmtScore = (n: number | null | undefined) => (n == null ? "—" : n.toFixed(2));

export function fmtDate(iso: string | null | undefined) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function fmtBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export function truncate(s: string, n: number) {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

export function normalizeMetrics(raw: unknown): Metrics {
  const r = (raw ?? {}) as Record<string, number>;
  return {
    faithfulness: Number(r.faithfulness ?? 0),
    answer_relevancy: Number(r.answer_relevancy ?? 0),
    context_precision: Number(r.context_precision ?? 0),
    context_recall: Number(r.context_recall ?? 0),
  };
}
