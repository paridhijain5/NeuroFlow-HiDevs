"use client";
import { useEffect, useState } from "react";
import { evaluationsStreamUrl, listEvaluations, normalizeEvaluation } from "@/lib/api";
import type { EvaluationRecord } from "@/lib/types";

/** Initial page from GET /evaluations, then live updates from GET /evaluations/stream (SSE). */
export function useEvaluationFeed() {
  const [items, setItems] = useState<EvaluationRecord[]>([]);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    listEvaluations()
      .then((rows) => alive && setItems(rows))
      .catch(() => undefined)
      .finally(() => alive && setLoading(false));

    const es = new EventSource(evaluationsStreamUrl());
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false); // EventSource auto-reconnects
    const onEvent = (e: MessageEvent) => {
      try {
        const rec = normalizeEvaluation(JSON.parse(e.data));
        setItems((prev) => [rec, ...prev.filter((p) => p.id !== rec.id)].slice(0, 200));
      } catch { /* ignore keep-alives */ }
    };
    es.addEventListener("evaluation", onEvent as EventListener);
    es.onmessage = onEvent;
    return () => { alive = false; es.close(); };
  }, []);

  return { items, connected, loading };
}
