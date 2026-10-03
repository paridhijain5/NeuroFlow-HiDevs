"use client";
import { useMemo, useState } from "react";
import type { StreamState } from "@/hooks/useSSEStream";
import MetricGauge from "@/components/MetricGauge";
import { METRICS, METRIC_LABELS } from "@/lib/config";
import type { Source } from "@/lib/types";
import CitationDrawer from "./CitationDrawer";
import FeedbackButtons from "./FeedbackButtons";
import RetrievalInspector from "./RetrievalInspector";

/** Resolve [n] markers in the answer (or the citations event) to source chunks. */
export function resolveCitations(state: StreamState): { n: number; source: Source }[] {
  const nums = new Set<number>();
  for (const m of state.answer.matchAll(/\[(\d+)\]/g)) nums.add(Number(m[1]));
  state.citations.forEach((c) => nums.add(c.index));
  const out: { n: number; source: Source }[] = [];
  [...nums].sort((a, b) => a - b).forEach((n) => {
    const cid = state.citations.find((c) => c.index === n)?.chunk_id;
    const src = (cid && state.sources.find((s) => s.chunk_id === cid)) || state.sources[n - 1];
    if (src) out.push({ n, source: src });
  });
  return out;
}

export default function ResponsePanel({
  title, runId, query, state,
}: { title: string; runId: string | null; query: string; state: StreamState }) {
  const [open, setOpen] = useState<Source | null>(null);
  const [inspector, setInspector] = useState(false);
  const citations = useMemo(() => resolveCitations(state), [state]);
  const streaming = state.status === "connecting" || state.status === "streaming";

  if (!runId) return null;
  return (
    <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <header className="flex items-center justify-between">
        <h3 className="font-semibold">{title}</h3>
        <span className="text-xs text-slate-500">
          {state.status === "connecting" && "Connecting…"}
          {state.status === "streaming" && "Streaming…"}
          {state.status === "done" && "Complete"}
        </span>
      </header>

      {/* 1. sources appear before the answer */}
      {state.sources.length > 0 && (
        <div>
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Retrieved sources ({state.sources.length})
          </div>
          <ul className="space-y-1.5">
            {state.sources.map((s, i) => (
              <li key={s.chunk_id}>
                <button onClick={() => setOpen(s)}
                  className="flex w-full items-center gap-3 rounded-md border border-slate-200 px-3 py-1.5 text-left text-sm hover:bg-slate-50">
                  <span className="font-mono text-xs text-indigo-600">[{i + 1}]</span>
                  <span className="flex-1 truncate">{s.document_name ?? s.chunk_id}</span>
                  <span className="h-1.5 w-20 overflow-hidden rounded-full bg-slate-100">
                    <span className="block h-full bg-indigo-500" style={{ width: `${Math.round(Math.min(s.score, 1) * 100)}%` }} />
                  </span>
                  <span className="w-10 text-right font-mono text-xs">{s.score.toFixed(2)}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 2. token-by-token answer */}
      {(state.answer || streaming) && (
        <div>
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Answer</div>
          <p className="whitespace-pre-wrap leading-relaxed">
            {state.answer}
            {streaming && <span className="ml-0.5 inline-block h-4 w-1.5 translate-y-0.5 animate-pulse bg-indigo-500" />}
          </p>
        </div>
      )}

      {state.status === "error" && (
        <div className="rounded-md bg-red-50 p-3 text-sm text-red-700">{state.error ?? "Something went wrong"}</div>
      )}

      {/* 3. citation chips after streaming */}
      {state.status === "done" && citations.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">Citations</span>
          {citations.map(({ n, source }) => (
            <button key={n} onClick={() => setOpen(source)}
              className="rounded-full bg-indigo-50 px-2.5 py-0.5 text-xs font-medium text-indigo-700 hover:bg-indigo-100"
              title={source.document_name ?? source.chunk_id}>
              [{n}] {source.document_name ?? source.chunk_id.slice(0, 8)}
            </button>
          ))}
        </div>
      )}

      {/* 4. evaluation gauges (~2s after done) */}
      {state.status === "done" && (
        <div>
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">Evaluation</div>
          {state.evaluation ? (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {METRICS.map((m) => <MetricGauge key={m} label={METRIC_LABELS[m]} value={state.evaluation!.metrics[m]} />)}
            </div>
          ) : (
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <span className="h-2 w-2 animate-ping rounded-full bg-indigo-500" /> Evaluating…
            </div>
          )}
        </div>
      )}

      {state.status === "done" && (
        <div className="flex items-center justify-between border-t border-slate-100 pt-3">
          <FeedbackButtons runId={runId} />
          <button onClick={() => setInspector((v) => !v)}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm hover:bg-slate-50">
            {inspector ? "Hide" : "Show"} Retrieval Inspector
          </button>
        </div>
      )}
      {inspector && <RetrievalInspector runId={runId} query={query} sources={state.sources} />}

      <CitationDrawer source={open} onClose={() => setOpen(null)} />
    </section>
  );
}
