"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { listDocuments } from "@/lib/api";
import type { DocumentRecord } from "@/lib/types";
import { fmtDate } from "@/lib/utils";
import UploadZone from "@/components/documents/UploadZone";
import StatusBadge from "@/components/documents/StatusBadge";
import DocumentDetail from "@/components/documents/DocumentDetail";

export default function DocumentsPage() {
  const { data = [], isLoading, isError } = useQuery({
    queryKey: ["documents"],
    queryFn: listDocuments,
    // keep polling while anything is still being processed
    refetchInterval: (q) => (q.state.data?.some((d) => d.status === "processing" || d.status === "pending") ? 3000 : false),
  });
  const [selected, setSelected] = useState<DocumentRecord | null>(null);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Documents</h1>
      <UploadZone />

      {isLoading && <p className="text-slate-500">Loading documents…</p>}
      {isError && <p className="text-red-600">Could not load documents.</p>}

      <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white shadow-sm">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-2">Filename</th><th className="px-4 py-2">Type</th><th className="px-4 py-2">Status</th>
              <th className="px-4 py-2 text-right">Chunks</th><th className="px-4 py-2">Ingested</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {data.map((d) => (
              <tr key={d.id} onClick={() => setSelected(d)} className="cursor-pointer hover:bg-slate-50">
                <td className="px-4 py-2 font-medium">{d.filename}</td>
                <td className="px-4 py-2 text-slate-600">{d.file_type}</td>
                <td className="px-4 py-2"><StatusBadge status={d.status} /></td>
                <td className="px-4 py-2 text-right font-mono">{d.chunk_count}</td>
                <td className="px-4 py-2 text-slate-600">{fmtDate(d.ingested_at)}</td>
              </tr>
            ))}
            {!isLoading && data.length === 0 && (
              <tr><td colSpan={5} className="px-4 py-8 text-center text-slate-500">No documents yet — upload one above.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <DocumentDetail doc={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
