"use client";
import { useEffect, useState } from "react";
import { scoreTone } from "@/lib/utils";

/** Circular gauge that animates from 0 to `value` (0-1) when it mounts. */
export default function MetricGauge({ label, value }: { label: string; value: number }) {
  const [shown, setShown] = useState(0);
  useEffect(() => {
    const t = setTimeout(() => setShown(value), 60);
    return () => clearTimeout(t);
  }, [value]);

  const r = 28;
  const c = 2 * Math.PI * r;
  const tone = scoreTone(value);
  return (
    <div className="flex flex-col items-center gap-1">
      <svg width="76" height="76" viewBox="0 0 76 76" role="img" aria-label={`${label} ${value.toFixed(2)}`}>
        <circle cx="38" cy="38" r={r} fill="none" stroke="#e2e8f0" strokeWidth="7" />
        <circle
          cx="38" cy="38" r={r} fill="none" stroke={tone.hex} strokeWidth="7" strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c * (1 - Math.min(Math.max(shown, 0), 1))}
          transform="rotate(-90 38 38)"
          style={{ transition: "stroke-dashoffset 1s ease-out" }}
        />
        <text x="38" y="43" textAnchor="middle" fontSize="15" fontWeight="600" fill="#0f172a">
          {value.toFixed(2)}
        </text>
      </svg>
      <span className="text-center text-xs text-slate-600">{label}</span>
    </div>
  );
}
