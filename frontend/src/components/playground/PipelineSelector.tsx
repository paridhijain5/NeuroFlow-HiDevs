"use client";
import type { Pipeline } from "@/lib/types";
import { fmtScore } from "@/lib/utils";

export default function PipelineSelector({
  pipelines, value, onChange, label,
}: { pipelines: Pipeline[]; value: string; onChange: (id: string) => void; label: string }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block font-medium text-slate-700">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-slate-900"
      >
        <option value="">Select a pipeline…</option>
        {pipelines.filter((p) => p.active !== false).map((p) => (
          <option key={p.id} value={p.id}>
            {p.name} (v{p.version}) — avg {fmtScore(p.avg_overall_score)}
          </option>
        ))}
      </select>
    </label>
  );
}
