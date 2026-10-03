"use client";
import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { uploadDocument } from "@/lib/api";
import { fmtBytes } from "@/lib/utils";

interface Item { id: string; file: File; progress: number; state: "uploading" | "done" | "error"; error?: string }

function icon(name: string) {
  const ext = name.split(".").pop()?.toLowerCase();
  if (ext === "pdf") return "📕";
  if (ext === "md" || ext === "txt") return "📝";
  if (ext === "docx" || ext === "doc") return "📘";
  if (ext === "csv" || ext === "xlsx") return "📊";
  if (ext === "html") return "🌐";
  return "📄";
}

export default function UploadZone() {
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [items, setItems] = useState<Item[]>([]);

  const patch = (id: string, p: Partial<Item>) => setItems((xs) => xs.map((x) => (x.id === id ? { ...x, ...p } : x)));

  const upload = (files: FileList | File[]) => {
    [...files].forEach(async (file) => {
      const id = `${file.name}-${file.size}-${Math.random().toString(36).slice(2, 7)}`;
      setItems((xs) => [...xs, { id, file, progress: 0, state: "uploading" }]);
      try {
        await uploadDocument(file, (pct) => patch(id, { progress: pct }));
        patch(id, { progress: 100, state: "done" });
        qc.invalidateQueries({ queryKey: ["documents"] });
      } catch (e) {
        patch(id, { state: "error", error: e instanceof Error ? e.message : "Upload failed" });
      }
    });
  };

  return (
    <div className="space-y-3">
      <div
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); upload(e.dataTransfer.files); }}
        onClick={() => input.current?.click()}
        className={`cursor-pointer rounded-xl border-2 border-dashed p-8 text-center transition ${
          over ? "border-indigo-500 bg-indigo-50" : "border-slate-300 bg-white hover:border-indigo-300"
        }`}
      >
        <div className="text-3xl">⬆️</div>
        <p className="mt-2 font-medium">Drag &amp; drop files here, or click to browse</p>
        <p className="text-xs text-slate-500">Multiple files supported</p>
        <input ref={input} type="file" multiple hidden onChange={(e) => e.target.files && upload(e.target.files)} />
      </div>

      {items.length > 0 && (
        <ul className="space-y-2">
          {items.map((it) => (
            <li key={it.id} className="rounded-lg border border-slate-200 bg-white p-3">
              <div className="flex items-center gap-3 text-sm">
                <span className="text-xl">{icon(it.file.name)}</span>
                <span className="flex-1 truncate font-medium">{it.file.name}</span>
                <span className="text-xs text-slate-500">{fmtBytes(it.file.size)}</span>
                <span className="w-16 text-right text-xs">
                  {it.state === "done" ? "✓ sent" : it.state === "error" ? "failed" : `${it.progress}%`}
                </span>
              </div>
              <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100">
                <div className={`h-full transition-all ${it.state === "error" ? "bg-red-500" : it.state === "done" ? "bg-emerald-500" : "bg-indigo-500"}`}
                  style={{ width: `${it.progress}%` }} />
              </div>
              {it.error && <p className="mt-1 text-xs text-red-600">{it.error}</p>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
