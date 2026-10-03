"use client";
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Background, Controls, ReactFlow, type Edge, type Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { getRunRetrieval } from "@/lib/api";
import type { RetrievalSummary, Source } from "@/lib/types";
import { truncate } from "@/lib/utils";

const DEFAULT_STRATEGIES = ["Vector search", "BM25 keyword", "Query expansion"];

function label(title: string, count: number | null, sub?: string) {
  return (
    <div className="text-center">
      <div className="text-xs font-semibold">{title}</div>
      {sub && <div className="text-[10px] text-slate-500">{sub}</div>}
      <div className="mt-0.5 text-[11px] text-indigo-700">{count == null ? "— chunks" : `${count} chunk${count === 1 ? "" : "s"}`}</div>
    </div>
  );
}

const box = (bg: string) => ({
  background: bg, border: "1px solid #cbd5e1", borderRadius: 10, padding: 8, width: 170, color: "#0f172a",
});

/** Go-further: Retrieval Inspector — React Flow diagram of the retrieval pipeline for one run. */
export default function RetrievalInspector({ runId, query, sources }: { runId: string; query: string; sources: Source[] }) {
  const { data, isLoading } = useQuery({ queryKey: ["retrieval", runId], queryFn: () => getRunRetrieval(runId) });

  const summary: RetrievalSummary = data ?? {
    strategies: DEFAULT_STRATEGIES.map((name) => ({ name, chunk_count: NaN })),
    rrf_count: NaN, reranked_count: NaN, final_count: sources.length,
  };
  const num = (n: number) => (Number.isFinite(n) ? n : null);

  const { nodes, edges } = useMemo(() => {
    const strategies = summary.strategies.slice(0, 3);
    const n: Node[] = [
      { id: "q", position: { x: 0, y: 110 }, data: { label: label("Query", null, truncate(query, 28)) }, style: box("#eef2ff") },
      ...strategies.map((s, i) => ({
        id: `s${i}`, position: { x: 250, y: i * 110 },
        data: { label: label(s.name, num(s.chunk_count)) }, style: box("#f0fdf4"),
      })),
      { id: "rrf", position: { x: 500, y: 110 }, data: { label: label("RRF fusion", num(summary.rrf_count)) }, style: box("#fffbeb") },
      { id: "rr", position: { x: 740, y: 110 }, data: { label: label("Reranker", num(summary.reranked_count)) }, style: box("#fdf2f8") },
      { id: "ctx", position: { x: 980, y: 110 }, data: { label: label("Final context", num(summary.final_count)) }, style: box("#ecfeff") },
    ];
    const e: Edge[] = [
      ...strategies.flatMap((_, i) => [
        { id: `q-s${i}`, source: "q", target: `s${i}`, animated: true },
        { id: `s${i}-rrf`, source: `s${i}`, target: "rrf", animated: true },
      ]),
      { id: "rrf-rr", source: "rrf", target: "rr", animated: true },
      { id: "rr-ctx", source: "rr", target: "ctx", animated: true },
    ];
    return { nodes: n, edges: e };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, query, sources.length]);

  return (
    <div className="mt-4 rounded-lg border border-slate-200">
      <div className="border-b border-slate-200 bg-slate-50 px-3 py-2 text-sm font-semibold">
        Retrieval Inspector {isLoading && <span className="font-normal text-slate-500">· loading…</span>}
        {!data && !isLoading && (
          <span className="ml-2 font-normal text-slate-500">(no /retrieval data — showing final context only)</span>
        )}
      </div>
      <div style={{ height: 380 }}>
        <ReactFlow nodes={nodes} edges={edges} fitView nodesDraggable={false} proOptions={{ hideAttribution: true }}>
          <Background />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>
    </div>
  );
}
