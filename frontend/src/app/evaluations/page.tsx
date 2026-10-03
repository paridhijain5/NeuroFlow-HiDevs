"use client";
import { useMemo, useState } from "react";
import { useEvaluationFeed } from "@/hooks/useEvaluationFeed";
import EvaluationCard from "@/components/evaluations/EvaluationCard";
import FilterBar, { EMPTY_FILTERS, type EvalFilters } from "@/components/evaluations/FilterBar";
import type { EvaluationRecord } from "@/lib/types";

function matches(e: EvaluationRecord, f: EvalFilters) {
  if (f.pipeline && e.pipeline_name !== f.pipeline) return false;
  if (f.metric && f.threshold !== "") {
    const v = f.metric === "overall_score" ? e.overall_score : e.metrics[f.metric as keyof typeof e.metrics];
    if (!(v < Number(f.threshold))) return false;
  }
  const t = new Date(e.created_at).getTime();
  if (f.from && t < new Date(f.from).getTime()) return false;
  if (f.to && t > new Date(f.to).getTime() + 86_400_000) return false;
  return true;
}

export default function EvaluationsPage() {
  const { items, connected, loading } = useEvaluationFeed();
  const [filters, setFilters] = useState<EvalFilters>(EMPTY_FILTERS);
  const pipelines = useMemo(() => [...new Set(items.map((i) => i.pipeline_name))].sort(), [items]);
  const shown = useMemo(() => items.filter((e) => matches(e, filters)), [items, filters]);

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Evaluation Feed</h1>
        <span className="flex items-center gap-2 text-sm text-slate-600">
          <span className="relative flex h-2.5 w-2.5">
            {connected && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />}
            <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${connected ? "bg-emerald-500" : "bg-slate-400"}`} />
          </span>
          {connected ? "Live" : "Reconnecting…"}
        </span>
      </div>

      <FilterBar value={filters} onChange={setFilters} pipelines={pipelines} />

      {loading && <p className="text-slate-500">Loading…</p>}
      {!loading && shown.length === 0 && (
        <p className="rounded-xl border border-dashed border-slate-300 p-8 text-center text-slate-500">
          No evaluations match yet. Run a query in the Playground and watch this feed.
        </p>
      )}
      <div className="space-y-3">
        {shown.map((e, i) => <EvaluationCard key={e.id} ev={e} fresh={i === 0 && connected} />)}
      </div>
    </div>
  );
}
