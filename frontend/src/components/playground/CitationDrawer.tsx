"use client";
import Drawer from "@/components/Drawer";
import type { Source } from "@/lib/types";

export default function CitationDrawer({ source, onClose }: { source: Source | null; onClose: () => void }) {
  return (
    <Drawer open={!!source} onClose={onClose} title="Source chunk">
      {source && (
        <div className="space-y-4 text-sm">
          <div>
            <div className="text-xs uppercase tracking-wide text-slate-500">Document</div>
            <div className="font-medium">{source.document_name ?? source.document_id ?? "Unknown"}</div>
          </div>
          <div className="flex gap-6">
            <div>
              <div className="text-xs uppercase tracking-wide text-slate-500">Chunk ID</div>
              <div className="font-mono text-xs">{source.chunk_id}</div>
            </div>
            <div>
              <div className="text-xs uppercase tracking-wide text-slate-500">Relevance</div>
              <div className="font-mono">{source.score.toFixed(3)}</div>
            </div>
          </div>
          <div>
            <div className="mb-1 text-xs uppercase tracking-wide text-slate-500">Content</div>
            <p className="whitespace-pre-wrap rounded-md bg-slate-50 p-3 leading-relaxed">{source.content}</p>
          </div>
          {source.metadata && Object.keys(source.metadata).length > 0 && (
            <div>
              <div className="mb-1 text-xs uppercase tracking-wide text-slate-500">Document metadata</div>
              <dl className="divide-y divide-slate-100 rounded-md border border-slate-200">
                {Object.entries(source.metadata).map(([k, v]) => (
                  <div key={k} className="flex gap-3 px-3 py-1.5">
                    <dt className="w-32 shrink-0 text-slate-500">{k}</dt>
                    <dd className="break-all">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}
        </div>
      )}
    </Drawer>
  );
}
