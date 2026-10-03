/**
 * Single place that knows the backend URL shapes.
 * If one of your earlier tasks used a different path / response shape, change it here only.
 */
import axios from "axios";
import { API_URL } from "./config";
import { normalizeMetrics } from "./utils";
import type {
  Chunk, DocumentRecord, EvaluationRecord, Pipeline, PipelineAnalytics,
  RetrievalSummary, RunEvaluation, SimilarChunk, Source,
} from "./types";

export const http = axios.create({ baseURL: API_URL });

// ------------------------------------------------------------------ pipelines
export const listPipelines = async (): Promise<Pipeline[]> => {
  const { data } = await http.get("/pipelines");
  const rows = Array.isArray(data) ? data : data.items ?? [];
  return rows.map((p: Record<string, unknown>) => ({
    ...p,
    daily_scores: ((p.daily_scores as unknown[]) ?? []).map((d) =>
      typeof d === "number" ? d : Number((d as { score: number }).score),
    ),
    query_count_7d: Number(p.query_count_7d ?? 0),
    avg_overall_score: p.avg_overall_score == null ? null : Number(p.avg_overall_score),
  })) as Pipeline[];
};

export const getPipelineSchema = async (): Promise<object> =>
  (await http.get("/pipelines/schema")).data;

export const createPipeline = async (config: unknown) =>
  (await http.post("/pipelines", config)).data;

export const getPipelineAnalytics = async (id: string): Promise<PipelineAnalytics> => {
  const d = (await http.get(`/pipelines/${id}/analytics`)).data;
  return { ...d, metrics: normalizeMetrics(d.metrics ?? d.avg_metrics) };
};

// ----------------------------------------------------------------- playground
export const startQuery = async (pipeline_id: string, query: string): Promise<string> => {
  const { data } = await http.post("/query", { pipeline_id, query, stream: true });
  return String(data.run_id ?? data.id);
};

export const streamUrl = (runId: string) => `${API_URL}/runs/${runId}/stream`;

export const rateRun = async (runId: string, rating: 1 | 5) =>
  (await http.patch(`/runs/${runId}/rating`, { rating })).data;

/** Returns null while the async evaluation isn't ready yet. */
export const getRunEvaluation = async (runId: string): Promise<RunEvaluation | null> => {
  try {
    const res = await http.get(`/runs/${runId}/evaluation`, { validateStatus: (s) => s < 500 });
    if (res.status !== 200 || !res.data) return null;
    const d = res.data;
    const metrics = normalizeMetrics(d.metrics ?? d.scores ?? d);
    const overall = d.overall_score ?? Object.values(metrics).reduce((a, b) => a + b, 0) / 4;
    return { metrics, overall_score: Number(overall) };
  } catch {
    return null;
  }
};

export const getRunRetrieval = async (runId: string): Promise<RetrievalSummary | null> => {
  try {
    return (await http.get(`/runs/${runId}/retrieval`)).data as RetrievalSummary;
  } catch {
    return null;
  }
};

// ---------------------------------------------------------------- evaluations
export const listEvaluations = async (): Promise<EvaluationRecord[]> => {
  const { data } = await http.get("/evaluations", { params: { limit: 50 } });
  const rows = Array.isArray(data) ? data : data.items ?? [];
  return rows.map(normalizeEvaluation);
};

export const evaluationsStreamUrl = () => `${API_URL}/evaluations/stream`;

export function normalizeEvaluation(e: Record<string, unknown>): EvaluationRecord {
  const metrics = normalizeMetrics(e.metrics ?? e.scores ?? e);
  const overall =
    e.overall_score != null
      ? Number(e.overall_score)
      : Object.values(metrics).reduce((a, b) => a + b, 0) / 4;
  return {
    id: String(e.id ?? e.evaluation_id ?? e.run_id ?? Math.random()),
    run_id: e.run_id ? String(e.run_id) : undefined,
    query: String(e.query ?? ""),
    answer: e.answer ? String(e.answer) : undefined,
    pipeline_id: e.pipeline_id ? String(e.pipeline_id) : undefined,
    pipeline_name: String(e.pipeline_name ?? e.pipeline ?? "unknown"),
    metrics,
    overall_score: overall,
    chunks: (e.chunks ?? e.retrieved_chunks) as Source[] | undefined,
    created_at: String(e.created_at ?? e.timestamp ?? new Date().toISOString()),
  };
}

// ------------------------------------------------------------------ documents
export const listDocuments = async (): Promise<DocumentRecord[]> => {
  const { data } = await http.get("/documents");
  return Array.isArray(data) ? data : data.items ?? [];
};

export const uploadDocument = async (file: File, onProgress: (pct: number) => void) => {
  const form = new FormData();
  form.append("file", file);
  return (
    await http.post("/ingest", form, {
      headers: { "Content-Type": "multipart/form-data" },
      onUploadProgress: (e) => e.total && onProgress(Math.round((e.loaded / e.total) * 100)),
    })
  ).data;
};

export const getDocumentChunks = async (id: string): Promise<Chunk[]> => {
  const { data } = await http.get(`/documents/${id}/chunks`);
  const rows = Array.isArray(data) ? data : data.items ?? [];
  return rows.map((c: Record<string, unknown>, i: number) => ({
    chunk_id: String(c.chunk_id ?? c.id),
    index: Number(c.index ?? c.chunk_index ?? i),
    content: String(c.content ?? c.text ?? ""),
  }));
};

export const findSimilarChunks = async (chunkId: string): Promise<SimilarChunk[]> => {
  const { data } = await http.get(`/chunks/${chunkId}/similar`, { params: { limit: 10 } });
  const rows = Array.isArray(data) ? data : data.items ?? [];
  return rows.map((r: Record<string, unknown>) => ({
    chunk_id: String(r.chunk_id ?? r.id),
    score: Number(r.score ?? r.similarity ?? 0),
  }));
};
