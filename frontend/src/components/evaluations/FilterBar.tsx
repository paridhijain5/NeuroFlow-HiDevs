"use client";
import { METRICS, METRIC_LABELS } from "@/lib/config";

export interface EvalFilters {
  pipeline: string;
  metric: string;       // "" = none
  threshold: string;    // e.g. "0.7"
  from: string;
  to: string;
}
export const EMPTY_FILTERS: EvalFilters = { pipeline: "", metric: "", threshold: "0.7", from: "", to: "" };

const input = "rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm text-slate-900";

export default function FilterBar({
  value, onChange, pipelines,
}: { value: EvalFilters; onChange: (f: EvalFilters) => void; pipelines: string[] }) {
  const set = (patch: Partial<EvalFilters>) => onChange({ ...value, ...patch });
  return (
    <div className="flex flex-wrap items-end gap-4 rounded-xl border border-slate-200 bg-white p-4">
      <label className="text-xs text-slate-600">Pipeline
        <select className={`${input} mt-1 block`} value={value.pipeline} onChange={(e) => set({ pipeline: e.target.value })}>
          <option value="">All</option>
          {pipelines.map((p) => <option key={p}>{p}</option>)}
        </select>
      </label>
      <div className="text-xs text-slate-600">Metric below threshold
        <div className="mt-1 flex items-center gap-1">
          <select className={input} value={value.metric} onChange={(e) => set({ metric: e.target.value })}>
            <option value="">Any</option>
            <option value="overall_score">Overall</option>
            {METRICS.map((m) => <option key={m} value={m}>{METRIC_LABELS[m]}</option>)}
          </select>
          <span className="text-base">&lt;</span>
          <input type="number" min={0} max={1} step={0.05} className={`${input} w-20`}
            value={value.threshold} onChange={(e) => set({ threshold: e.target.value })} />
        </div>
      </div>
      <label className="text-xs text-slate-600">From
        <input type="date" className={`${input} mt-1 block`} value={value.from} onChange={(e) => set({ from: e.target.value })} />
      </label>
      <label className="text-xs text-slate-600">To
        <input type="date" className={`${input} mt-1 block`} value={value.to} onChange={(e) => set({ to: e.target.value })} />
      </label>
      <button onClick={() => onChange(EMPTY_FILTERS)} className="text-sm text-indigo-600 hover:underline">Reset</button>
    </div>
  );
}
