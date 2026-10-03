"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { listPipelines } from "@/lib/api";
import type { Pipeline } from "@/lib/types";
import PipelineCard from "@/components/pipelines/PipelineCard";
import CreatePipelineModal from "@/components/pipelines/CreatePipelineModal";
import AnalyticsDrawer from "@/components/pipelines/AnalyticsDrawer";

export default function PipelinesPage() {
  const { data = [], isLoading, isError } = useQuery({ queryKey: ["pipelines"], queryFn: listPipelines });
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<Pipeline | null>(null);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Pipeline Manager</h1>
        <button onClick={() => setCreating(true)} className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700">
          + Create pipeline
        </button>
      </div>

      <div className="flex gap-4 text-xs text-slate-600">
        <span><b className="text-emerald-600">●</b> score &gt; 0.8</span>
        <span><b className="text-amber-500">●</b> 0.6 – 0.8</span>
        <span><b className="text-red-500">●</b> &lt; 0.6</span>
      </div>

      {isLoading && <p className="text-slate-500">Loading pipelines…</p>}
      {isError && <p className="text-red-600">Could not load pipelines.</p>}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {data.map((p) => <PipelineCard key={p.id} p={p} onClick={() => setSelected(p)} />)}
      </div>

      {creating && <CreatePipelineModal onClose={() => setCreating(false)} />}
      <AnalyticsDrawer pipeline={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
