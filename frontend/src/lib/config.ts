export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export const METRICS = [
  "faithfulness",
  "answer_relevancy",
  "context_precision",
  "context_recall",
] as const;

export const METRIC_LABELS: Record<(typeof METRICS)[number], string> = {
  faithfulness: "Faithfulness",
  answer_relevancy: "Answer relevancy",
  context_precision: "Context precision",
  context_recall: "Context recall",
};
