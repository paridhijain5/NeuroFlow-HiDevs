import { METRICS, METRIC_LABELS } from "@/lib/config";
import type { RunEvaluation } from "@/lib/types";
import { scoreTone } from "@/lib/utils";

function Cell({ v, win }: { v: number; win: boolean }) {
  const t = scoreTone(v);
  return (
    <td className="px-3 py-2">
      <div className="flex items-center gap-2">
        <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
          <div className={`h-full ${t.bar}`} style={{ width: `${Math.round(v * 100)}%` }} />
        </div>
        <span className={`w-10 text-right font-mono text-xs ${win ? "font-bold" : ""}`}>{v.toFixed(2)}</span>
      </div>
    </td>
  );
}

export default function ScoreCompare({
  a, b, nameA, nameB,
}: { a: RunEvaluation; b: RunEvaluation; nameA: string; nameB: string }) {
  const rows = [
    ...METRICS.map((m) => ({ label: METRIC_LABELS[m], x: a.metrics[m], y: b.metrics[m] })),
    { label: "Overall", x: a.overall_score, y: b.overall_score },
  ];
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h3 className="mb-3 font-semibold">Evaluation scorecard</h3>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
            <th className="px-3 py-1">Metric</th><th className="px-3 py-1">{nameA}</th><th className="px-3 py-1">{nameB}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">
          {rows.map((r) => (
            <tr key={r.label}>
              <td className="px-3 py-2 font-medium">{r.label}</td>
              <Cell v={r.x} win={r.x > r.y} />
              <Cell v={r.y} win={r.y > r.x} />
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
