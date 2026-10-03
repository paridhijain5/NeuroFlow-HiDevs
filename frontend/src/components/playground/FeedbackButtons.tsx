"use client";
import { useState } from "react";
import { rateRun } from "@/lib/api";

export default function FeedbackButtons({ runId }: { runId: string }) {
  const [sel, setSel] = useState<1 | 5 | null>(null);
  const [err, setErr] = useState(false);

  const rate = async (r: 1 | 5) => {
    const prev = sel;
    setSel(r);
    setErr(false);
    try { await rateRun(runId, r); } catch { setSel(prev); setErr(true); }
  };

  const base = "rounded-md border px-3 py-1.5 text-sm";
  return (
    <div className="flex items-center gap-2">
      <button onClick={() => rate(5)} aria-label="Thumbs up"
        className={`${base} ${sel === 5 ? "border-emerald-500 bg-emerald-50" : "border-slate-300 hover:bg-slate-50"}`}>👍</button>
      <button onClick={() => rate(1)} aria-label="Thumbs down"
        className={`${base} ${sel === 1 ? "border-red-500 bg-red-50" : "border-slate-300 hover:bg-slate-50"}`}>👎</button>
      {err && <span className="text-xs text-red-600">Could not save rating</span>}
    </div>
  );
}
