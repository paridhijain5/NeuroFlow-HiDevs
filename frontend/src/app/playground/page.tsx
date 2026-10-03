"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { listPipelines, startQuery } from "@/lib/api";
import { usePlayground } from "@/lib/store";
import { useSSEStream } from "@/hooks/useSSEStream";
import QueryInput from "@/components/playground/QueryInput";
import ResponsePanel from "@/components/playground/ResponsePanel";
import DiffView from "@/components/playground/DiffView";
import ScoreCompare from "@/components/playground/ScoreCompare";

export default function PlaygroundPage() {
  const { data: pipelines = [], isError } = useQuery({ queryKey: ["pipelines"], queryFn: listPipelines });
  const { query, compare, pipelineA, pipelineB } = usePlayground();

  const [runA, setRunA] = useState<string | null>(null);
  const [runB, setRunB] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState("");
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Each hook opens its own EventSource -> compare mode = two simultaneous SSE connections.
  const a = useSSEStream(runA);
  const b = useSSEStream(runB);

  const nameOf = (id: string) => pipelines.find((p) => p.id === id)?.name ?? "Pipeline";
  const busy = starting || ["connecting", "streaming"].includes(a.status) || ["connecting", "streaming"].includes(b.status);

  const submit = async () => {
    setError(null);
    setStarting(true);
    setRunA(null);
    setRunB(null);
    setSubmitted(query);
    try {
      const [ida, idb] = await Promise.all([
        startQuery(pipelineA, query),
        compare ? startQuery(pipelineB, query) : Promise.resolve(null),
      ]);
      setRunA(ida);
      setRunB(idb);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start query");
    } finally {
      setStarting(false);
    }
  };

  const bothDone = compare && a.status === "done" && b.status === "done";

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Query Playground</h1>
      {isError && <div className="rounded-md bg-amber-50 p-3 text-sm text-amber-800">Could not load pipelines — is the backend running?</div>}
      <QueryInput pipelines={pipelines} onSubmit={submit} busy={busy} />
      {error && <div className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</div>}

      <div className={compare ? "grid gap-4 lg:grid-cols-2" : ""}>
        <ResponsePanel title={compare ? `A · ${nameOf(pipelineA)}` : nameOf(pipelineA)} runId={runA} query={submitted} state={a} />
        {compare && <ResponsePanel title={`B · ${nameOf(pipelineB)}`} runId={runB} query={submitted} state={b} />}
      </div>

      {bothDone && <DiffView a={a.answer} b={b.answer} nameA={nameOf(pipelineA)} nameB={nameOf(pipelineB)} />}
      {bothDone && a.evaluation && b.evaluation && (
        <ScoreCompare a={a.evaluation} b={b.evaluation} nameA={nameOf(pipelineA)} nameB={nameOf(pipelineB)} />
      )}
    </div>
  );
}
