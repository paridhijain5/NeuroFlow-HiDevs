import type { METRICS } from "./config";

export type MetricKey = (typeof METRICS)[number];
export type Metrics = Record<MetricKey, number>;

export interface Pipeline {
  id: string;
  name: string;
  version: string | number;
  active?: boolean;
  avg_overall_score: number | null;
  query_count_7d: number;
  daily_scores: number[];
}

export interface Source {
  chunk_id: string;
  document_id?: string;
  document_name?: string;
  content: string;
  score: number;
  metadata?: Record<string, unknown>;
}

export interface Citation {
  index: number;
  chunk_id: string;
}

export interface RunEvaluation {
  metrics: Metrics;
  overall_score: number;
}

export interface EvaluationRecord {
  id: string;
  run_id?: string;
  query: string;
  answer?: string;
  pipeline_id?: string;
  pipeline_name: string;
  metrics: Metrics;
  overall_score: number;
  chunks?: Source[];
  created_at: string;
}

export interface PipelineAnalytics {
  latency: { p50: number; p95: number; p99: number }; // ms
  cost_trend: { date: string; cost: number }[]; // last 30 days
  metrics: Metrics; // averages, for the radar chart
  failed_runs: { id: string; query: string; error: string; created_at: string }[];
}

export interface RetrievalSummary {
  strategies: { name: string; chunk_count: number }[];
  rrf_count: number;
  reranked_count: number;
  final_count: number;
}

export type DocStatus = "pending" | "processing" | "completed" | "failed";

export interface DocumentRecord {
  id: string;
  filename: string;
  file_type: string;
  status: DocStatus;
  chunk_count: number;
  ingested_at: string | null;
}

export interface Chunk {
  chunk_id: string;
  index: number;
  content: string;
}

export interface SimilarChunk {
  chunk_id: string;
  score: number;
}
