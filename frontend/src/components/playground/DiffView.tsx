"use client";
import { useMemo } from "react";
import { diffWords } from "@/lib/diff";

export default function DiffView({ a, b, nameA, nameB }: { a: string; b: string; nameA: string; nameB: string }) {
  const parts = useMemo(() => diffWords(a, b), [a, b]);
  return (
    <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-3 flex flex-wrap items-center gap-4">
        <h3 className="font-semibold">Where the answers diverge</h3>
        <span className="rounded bg-red-100 px-2 py-0.5 text-xs text-red-800">only in {nameA}</span>
        <span className="rounded bg-emerald-100 px-2 py-0.5 text-xs text-emerald-800">only in {nameB}</span>
      </div>
      <p className="whitespace-pre-wrap leading-relaxed">
        {parts.map((p, i) =>
          p.type === "same" ? <span key={i}>{p.text}</span>
          : p.type === "removed" ? <span key={i} className="rounded bg-red-100 px-0.5 text-red-800 line-through decoration-red-400">{p.text}</span>
          : <span key={i} className="rounded bg-emerald-100 px-0.5 text-emerald-800">{p.text}</span>,
        )}
      </p>
    </section>
  );
}
