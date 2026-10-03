"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { getRunEvaluation, streamUrl } from "@/lib/api";
import type { Citation, RunEvaluation, Source } from "@/lib/types";

export type StreamStatus = "idle" | "connecting" | "streaming" | "done" | "error";

export interface StreamState {
  status: StreamStatus;
  sources: Source[];
  answer: string;
  citations: Citation[];
  evaluation: RunEvaluation | null;
  error?: string;
}

export const EMPTY_STREAM: StreamState = {
  status: "idle", sources: [], answer: "", citations: [], evaluation: null,
};

function parse(raw: string): unknown {
  try { return JSON.parse(raw); } catch { return raw; }
}

/**
 * useSSEStream(runId) — opens GET /runs/{runId}/stream and exposes:
 *  sources (arrive first) -> answer (token by token) -> citations -> evaluation (~2s after done).
 *
 * Event protocol (named SSE events, or JSON {type, ...} on the default channel):
 *   sources   [{chunk_id, content, score, ...}]
 *   token     "text" | {token|text|content}
 *   citations [{index, chunk_id}]
 *   done
 *   error     "message" | {message}
 */
export function useSSEStream(runId: string | null): StreamState {
  // State is keyed by runId so a new run starts clean without a synchronous reset in an effect.
  const [store, setStore] = useState<{ id: string | null; data: StreamState }>({ id: null, data: EMPTY_STREAM });
  const finished = useRef(false);

  const setState = useCallback(
    (fn: (s: StreamState) => StreamState) =>
      setStore((prev) => {
        const base = prev.id === runId ? prev.data : { ...EMPTY_STREAM, status: "connecting" as const };
        return { id: runId, data: fn(base) };
      }),
    [runId],
  );
  const state: StreamState = !runId ? EMPTY_STREAM : store.id === runId ? store.data : { ...EMPTY_STREAM, status: "connecting" };

  useEffect(() => {
    finished.current = false;
    if (!runId) return;

    const es = new EventSource(streamUrl(runId));

    const handle = (type: string, raw: string) => {
      const data = parse(raw);
      const obj = (data && typeof data === "object" && !Array.isArray(data) ? data : {}) as Record<string, unknown>;
      let t = type;
      if (t === "message") t = String(obj.type ?? "token");

      if (t === "sources") {
        const list = Array.isArray(data) ? data : (obj.sources as unknown[]) ?? [];
        setState((s) => ({ ...s, status: "streaming", sources: list as Source[] }));
      } else if (t === "token") {
        const text = typeof data === "string" ? data : String(obj.token ?? obj.text ?? obj.content ?? "");
        setState((s) => ({ ...s, status: "streaming", answer: s.answer + text }));
      } else if (t === "citations") {
        const list = Array.isArray(data) ? data : (obj.citations as unknown[]) ?? [];
        setState((s) => ({ ...s, citations: list as Citation[] }));
      } else if (t === "done") {
        finished.current = true;
        es.close();
        setState((s) => ({ ...s, status: "done" }));
      } else if (t === "error") {
        finished.current = true;
        es.close();
        const msg = typeof data === "string" ? data : String(obj.message ?? "Stream error");
        setState((s) => ({ ...s, status: "error", error: msg }));
      }
    };

    for (const t of ["sources", "token", "citations", "done", "error", "message"]) {
      es.addEventListener(t, (e) => handle(t, (e as MessageEvent).data));
    }
    es.onerror = () => {
      es.close();
      if (!finished.current) {
        setState((s) => (s.status === "done" ? s : { ...s, status: "error", error: s.error ?? "Connection lost" }));
      }
    };
    return () => { finished.current = true; es.close(); };
  }, [runId, setState]);

  // Async evaluation: appears ~2s after generation finishes.
  useEffect(() => {
    if (!runId || state.status !== "done" || state.evaluation) return;
    let cancelled = false;
    let tries = 0;
    const poll = async () => {
      const ev = await getRunEvaluation(runId);
      if (cancelled) return;
      if (ev) { setState((s) => ({ ...s, evaluation: ev })); return; }
      if (++tries < 20) setTimeout(poll, 1000);
    };
    const t = setTimeout(poll, 2000);
    return () => { cancelled = true; clearTimeout(t); };
  }, [runId, state.status, state.evaluation, setState]);

  return state;
}
