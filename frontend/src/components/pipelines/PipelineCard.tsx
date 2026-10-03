"use client";
import { Line, LineChart, ResponsiveContainer, YAxis } from "recharts";
import type { Pipeline } from "@/lib/types";
import { fmtScore, scoreTone } from "@/lib/utils";

export default function PipelineCard({ p, onClick }: { p: Pipeline; onClick: () => void }) {
  const tone = scoreTone(p.avg_overall_score);
  const data = p.daily_scores.map((v, i) => ({ i, v }));
  return (
    <button onClick={onClick}
      className="rounded-xl border border-slate-200 bg-white p-4 text-left shadow-sm transition hover:border-indigo-300 hover:shadow">
      <div className="flex items-start justify-between">
        <div>
          <div className="font-semibold">{p.name}</div>
          <div className="text-xs text-slate-500">version {p.version}</div>
        </div>
        <span className={`rounded-full px-2.5 py-0.5 text-sm font-bold ${tone.bg} ${tone.text}`}>
          {fmtScore(p.avg_overall_score)}
        </span>
      </div>
      <div className="mt-3 text-sm text-slate-600">
        <span className="font-semibold text-slate-900">{p.query_count_7d}</span> queries · last 7 days
      </div>
      <div className="mt-2 h-12">
        {data.length > 1 ? (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data}>
              <YAxis hide domain={[0, 1]} />
              <Line type="monotone" dataKey="v" stroke={tone.hex} strokeWidth={2} dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className="pt-3 text-xs text-slate-400">Not enough data for a trend yet</div>
        )}
      </div>
    </button>
  );
}
