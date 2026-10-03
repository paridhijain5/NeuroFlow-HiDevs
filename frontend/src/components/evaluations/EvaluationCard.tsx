"use client";
import { useState } from "react";
import MetricBar from "@/components/MetricBar";
import ScoreBadge from "@/components/ScoreBadge";
import { METRICS, METRIC_LABELS } from "@/lib/config";
import type { EvaluationRecord } from "@/lib/types";
import { fmtDate, truncate } from "@/lib/utils";

export default function EvaluationCard({ ev, fresh }: { ev: EvaluationRecord; fresh?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <article className={`rounded-xl border bg-white p-4 shadow-sm ${fresh ? "border-indigo-300" : "border-slate-200"}`}>
      <button onClick={() => setOpen((o) => !o)} className="w-full text-left">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="font-medium">{open ? ev.query : truncate(ev.query, 100)}</div>
            <div className="mt-0.5 text-xs text-slate-500">{ev.pipeline_name} · {fmtDate(ev.created_at)}</div>
          </div>
          <ScoreBadge score={ev.overall_score} />
        </div>
        <div className="mt-3 grid gap-1.5 md:grid-cols-2">
          {METRICS.map((m) => <MetricBar key={m} label={METRIC_LABELS[m]} value={ev.metrics[m]} />)}
        </div>
      </button>

      {open && (
        <div className="mt-4 space-y-4 border-t border-slate-100 pt-4 text-sm">
          <div>
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">Generated answer</div>
            <p className="whitespace-pre-wrap">{ev.answer ?? <span className="text-slate-400">Not included in this event</span>}</p>
          </div>
          <div>
            <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
              Retrieved chunks ({ev.chunks?.length ?? 0})
            </div>
            <ul className="space-y-2">
              {(ev.chunks ?? []).map((c, i) => (
                <li key={c.chunk_id ?? i} className="rounded-md bg-slate-50 p-3">
                  <div className="mb-1 text-xs text-slate-500">
                    {c.document_name ?? c.chunk_id} · score {Number(c.score ?? 0).toFixed(2)}
                  </div>
                  <p className="whitespace-pre-wrap">{c.content}</p>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </article>
  );
}
