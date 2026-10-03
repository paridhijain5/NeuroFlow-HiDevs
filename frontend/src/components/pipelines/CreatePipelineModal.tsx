"use client";
import { useState } from "react";
import Editor, { type BeforeMount, type OnValidate } from "@monaco-editor/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import { createPipeline, getPipelineSchema } from "@/lib/api";

/**
 * Fallback used only if GET /pipelines/schema isn't available.
 * Best: expose it in FastAPI with `PipelineConfig.model_json_schema()` so the editor
 * validates against the real Pydantic schema.
 */
const FALLBACK_SCHEMA = {
  type: "object",
  required: ["name"],
  properties: {
    name: { type: "string", minLength: 1 },
    description: { type: "string" },
    chunking: {
      type: "object",
      properties: { chunk_size: { type: "integer", minimum: 50 }, chunk_overlap: { type: "integer", minimum: 0 } },
    },
    retrieval: {
      type: "object",
      properties: { top_k: { type: "integer", minimum: 1, maximum: 100 }, strategies: { type: "array", items: { type: "string" } } },
    },
    llm: {
      type: "object",
      properties: { provider: { type: "string" }, model: { type: "string" }, temperature: { type: "number", minimum: 0, maximum: 2 } },
    },
    rate_limit_rpm: { type: "integer", minimum: 1 },
  },
};

const TEMPLATE = `{
  "name": "my-pipeline",
  "description": "",
  "chunking": { "chunk_size": 512, "chunk_overlap": 64 },
  "retrieval": { "top_k": 5, "strategies": ["vector", "bm25"] },
  "llm": { "provider": "openai", "model": "gpt-4o-mini", "temperature": 0.2 },
  "rate_limit_rpm": 60
}`;

function backendErrors(e: unknown): string[] {
  if (isAxiosError(e)) {
    const d = e.response?.data?.detail;
    if (Array.isArray(d)) return d.map((x) => `${(x.loc ?? []).filter((l: string) => l !== "body").join(".")}: ${x.msg}`);
    if (typeof d === "string") return [d];
    return [e.message];
  }
  return [e instanceof Error ? e.message : "Unknown error"];
}

export default function CreatePipelineModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [text, setText] = useState(TEMPLATE);
  const [markers, setMarkers] = useState<{ line: number; message: string }[]>([]);
  const [serverErrors, setServerErrors] = useState<string[]>([]);

  const { data: schema } = useQuery({ queryKey: ["pipeline-schema"], queryFn: getPipelineSchema, retry: false });

  const beforeMount: BeforeMount = (monaco) => {
    monaco.languages.json.jsonDefaults.setDiagnosticsOptions({
      validate: true,
      allowComments: false,
      schemas: [{ uri: "https://neuroflow.local/pipeline-config.json", fileMatch: ["*"], schema: schema ?? FALLBACK_SCHEMA }],
    });
  };
  const onValidate: OnValidate = (m) => setMarkers(m.map((x) => ({ line: x.startLineNumber, message: x.message })));

  const create = useMutation({
    mutationFn: async () => createPipeline(JSON.parse(text)),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["pipelines"] }); onClose(); },
    onError: (e) => setServerErrors(backendErrors(e)),
  });

  const invalid = markers.length > 0;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-slate-900/40" onClick={onClose} />
      <div className="relative w-full max-w-3xl rounded-xl bg-white p-5 shadow-xl">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-semibold">Create pipeline</h2>
          <button onClick={onClose} className="rounded p-1 text-slate-500 hover:bg-slate-100" aria-label="Close">✕</button>
        </div>

        <div className="overflow-hidden rounded-md border border-slate-300">
          {/* key forces a re-mount when the real schema arrives so it is picked up */}
          <Editor key={schema ? "remote" : "fallback"} height="360px" language="json" value={text}
            onChange={(v) => { setText(v ?? ""); setServerErrors([]); }}
            beforeMount={beforeMount} onValidate={onValidate}
            options={{ minimap: { enabled: false }, fontSize: 13, scrollBeyondLastLine: false, automaticLayout: true }} />
        </div>

        <div className="mt-3 min-h-[3rem] text-sm">
          {invalid ? (
            <ul className="space-y-1 text-red-700">
              {markers.slice(0, 5).map((m, i) => <li key={i}>Line {m.line}: {m.message}</li>)}
            </ul>
          ) : serverErrors.length ? (
            <ul className="space-y-1 text-red-700">{serverErrors.map((m, i) => <li key={i}>{m}</li>)}</ul>
          ) : (
            <span className="text-emerald-700">✓ Valid against the PipelineConfig schema</span>
          )}
        </div>

        <div className="mt-3 flex justify-end gap-2">
          <button onClick={onClose} className="rounded-md border border-slate-300 px-4 py-2 text-sm">Cancel</button>
          <button onClick={() => create.mutate()} disabled={invalid || create.isPending}
            className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50">
            {create.isPending ? "Creating…" : "Create pipeline"}
          </button>
        </div>
      </div>
    </div>
  );
}
