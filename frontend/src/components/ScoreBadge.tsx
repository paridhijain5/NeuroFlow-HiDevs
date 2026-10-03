import { fmtScore, scoreTone } from "@/lib/utils";

export default function ScoreBadge({ score }: { score: number | null }) {
  const t = scoreTone(score);
  return (
    <span className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold ${t.bg} ${t.text}`}>
      {fmtScore(score)}
    </span>
  );
}
