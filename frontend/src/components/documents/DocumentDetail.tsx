"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import Drawer from "@/components/Drawer";
import { findSimilarChunks, getDocumentChunks } from "@/lib/api";
import type { DocumentRecord, SimilarChunk } from "@/lib/types";

export default function DocumentDetail({ doc, onClose }: { doc: DocumentRecord | null; onClose: () => void }) {
  const [similar, setSimilar] = useState<Record<string, SimilarChunk>>({});
  const [anchor, setAnchor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const { data: chunks = [], isLoading } = useQuery({
    queryKey: ["chunks", doc?.id], queryFn: () => getDocumentChunks(doc!.id), enabled: !!doc,
  });

  const find = async (chunkId: string) => {
    setBusy(true); setErr(null); setAnchor(chunkId);
    try {
      const res = await findSimilarChunks(chunkId);
      setSimilar(Object.fromEntries(res.filter((r) => r.chunk_id !== chunkId).map((r) => [r.chunk_id, r])));
    } catch { setErr("Similarity search failed"); setSimilar({}); }
    finally { setBusy(false); }
  };

  const close = () => { setSimilar({}); setAnchor(null); onClose(); };
  const hits = Object.keys(similar).length;
  const hitsHere = chunks.filter((c) => similar[c.chunk_id]).length;

  return (
    <Drawer open={!!doc} onClose={close} title={doc?.filename ?? ""} width="max-w-2xl">
      {isLoading && <p className="text-sm text-slate-500">Loading chunks…</p>}
      {anchor && (
        <p className="mb-3 rounded-md bg-indigo-50 p-2 text-xs text-indigo-800">
          {busy ? "Searching across all chunks…" : `${hits} similar chunks found across all documents (${hitsHere} in this document, highlighted).`}
        </p>
      )}
      {err && <p className="mb-3 text-sm text-red-600">{err}</p>}
      <ul className="space-y-2">
        {chunks.map((c) => {
          const sim = similar[c.chunk_id];
          const isAnchor = c.chunk_id === anchor;
          return (
            <li key={c.chunk_id}
              className={`rounded-lg border p-3 text-sm ${
                isAnchor ? "border-indigo-500 bg-indigo-50" : sim ? "border-amber-400 bg-amber-50" : "border-slate-200"
              }`}>
              <div className="mb-1 flex items-center justify-between text-xs text-slate-500">
                <span>Chunk #{c.index}</span>
                <span className="flex items-center gap-2">
                  {sim && <span className="rounded bg-amber-200 px-1.5 py-0.5 font-semibold text-amber-900">similarity {sim.score.toFixed(2)}</span>}
                  <button onClick={() => find(c.chunk_id)} disabled={busy} className="text-indigo-600 hover:underline disabled:opacity-50">
                    Find similar chunks
                  </button>
                </span>
              </div>
              <p className="whitespace-pre-wrap">{c.content}</p>
            </li>
          );
        })}
      </ul>
    </Drawer>
  );
}
