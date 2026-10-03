"use client";
import { useQuery } from "@tanstack/react-query";
import {
  Bar, BarChart, CartesianGrid, Line, LineChart, PolarAngleAxis, PolarGrid, PolarRadiusAxis,
  Radar, RadarChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import Drawer from "@/components/Drawer";
import { getPipelineAnalytics } from "@/lib/api";
import { METRICS, METRIC_LABELS } from "@/lib/config";
import type { Pipeline } from "@/lib/types";
import { fmtDate, truncate } from "@/lib/utils";

const H = ({ children }: { children: React.ReactNode }) => (
  <h3 className="mb-2 mt-6 text-sm font-semibold text-slate-700 first:mt-0">{children}</h3>
);

export default function AnalyticsDrawer({ pipeline, onClose }: { pipeline: Pipeline | null; onClose: () => void }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["analytics", pipeline?.id],
    queryFn: () => getPipelineAnalytics(pipeline!.id),
    enabled: !!pipeline,
  });

  return (
    <Drawer open={!!pipeline} onClose={onClose} title={pipeline ? `${pipeline.name} · analytics` : ""} width="max-w-2xl">
      {isLoading && <p className="text-sm text-slate-500">Loading analytics…</p>}
      {isError && <p className="text-sm text-red-600">Could not load analytics for this pipeline.</p>}
      {data && (
        <div>
          <H>Latency percentiles (ms)</H>
          <div className="h-52">
            <ResponsiveContainer>
              <BarChart data={[
                { name: "P50", ms: data.latency.p50 }, { name: "P95", ms: data.latency.p95 }, { name: "P99", ms: data.latency.p99 },
              ]}>
                <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="name" /><YAxis /><Tooltip />
                <Bar dataKey="ms" fill="#6366f1" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          <H>Cost per query · last 30 days</H>
          <div className="h-52">
            <ResponsiveContainer>
              <LineChart data={data.cost_trend}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" tickFormatter={(d: string) => d.slice(5)} /><YAxis /><Tooltip />
                <Line type="monotone" dataKey="cost" stroke="#10b981" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <H>Evaluation metrics</H>
          <div className="h-64">
            <ResponsiveContainer>
              <RadarChart data={METRICS.map((m) => ({ metric: METRIC_LABELS[m], value: data.metrics[m] }))}>
                <PolarGrid /><PolarAngleAxis dataKey="metric" tick={{ fontSize: 11 }} />
                <PolarRadiusAxis domain={[0, 1]} tick={{ fontSize: 10 }} />
                <Radar dataKey="value" stroke="#6366f1" fill="#6366f1" fillOpacity={0.35} />
              </RadarChart>
            </ResponsiveContainer>
          </div>

          <H>Recent failed runs</H>
          {data.failed_runs.length === 0 ? (
            <p className="text-sm text-slate-500">No failed runs 🎉</p>
          ) : (
            <ul className="space-y-2">
              {data.failed_runs.map((r) => (
                <li key={r.id} className="rounded-md border border-red-200 bg-red-50 p-3 text-sm">
                  <div className="font-medium">{truncate(r.query, 90)}</div>
                  <div className="mt-1 font-mono text-xs text-red-700">{r.error}</div>
                  <div className="mt-1 text-xs text-slate-500">{fmtDate(r.created_at)}</div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Drawer>
  );
}
