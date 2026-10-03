import { scoreTone } from "@/lib/utils";

export default function MetricBar({ label, value }: { label: string; value: number }) {
  const tone = scoreTone(value);
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-32 shrink-0 text-slate-600">{label}</span>
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-100">
        <div className={`h-full rounded-full ${tone.bar}`} style={{ width: `${Math.round(value * 100)}%` }} />
      </div>
      <span className="w-9 text-right font-mono text-slate-700">{value.toFixed(2)}</span>
    </div>
  );
}
