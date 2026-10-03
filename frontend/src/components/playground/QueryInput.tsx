"use client";
import type { Pipeline } from "@/lib/types";
import { usePlayground } from "@/lib/store";
import PipelineSelector from "./PipelineSelector";

const MAX = 2000;

export default function QueryInput({
  pipelines, onSubmit, busy,
}: { pipelines: Pipeline[]; onSubmit: () => void; busy: boolean }) {
  const s = usePlayground();
  const ready = s.query.trim() && s.pipelineA && (!s.compare || s.pipelineB);

  return (
    <div className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className={`grid gap-4 ${s.compare ? "md:grid-cols-2" : ""}`}>
        <PipelineSelector pipelines={pipelines} value={s.pipelineA} onChange={s.setPipelineA}
          label={s.compare ? "Pipeline A" : "Pipeline"} />
        {s.compare && (
          <PipelineSelector pipelines={pipelines} value={s.pipelineB} onChange={s.setPipelineB} label="Pipeline B" />
        )}
      </div>

      <div>
        <textarea
          value={s.query}
          onChange={(e) => s.setQuery(e.target.value.slice(0, MAX))}
          rows={4}
          placeholder="Ask a question about your documents…"
          className="w-full resize-y rounded-md border border-slate-300 bg-white px-3 py-2 text-slate-900"
        />
        <div className="mt-1 text-right text-xs text-slate-500">{s.query.length} / {MAX}</div>
      </div>

      <div className="flex items-center justify-between">
        <label className="flex cursor-pointer items-center gap-2 text-sm">
          <input type="checkbox" checked={s.compare} onChange={(e) => s.setCompare(e.target.checked)} className="h-4 w-4" />
          Compare mode
        </label>
        <button
          onClick={onSubmit}
          disabled={!ready || busy}
          className="rounded-md bg-indigo-600 px-5 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "Running…" : s.compare ? "Run both" : "Run query"}
        </button>
      </div>
    </div>
  );
}
