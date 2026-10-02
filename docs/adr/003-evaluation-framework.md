# ADR 003: Automated LLM-as-Judge Evaluation

## Context
We must score every generation (faithfulness, answer relevance, context precision, context recall). Human annotation is the gold standard but is slow, expensive, and cannot cover every production query.

## Decision
Use **automated LLM-as-judge evaluation on every generation**, run asynchronously, with a **small human-annotated calibration set** (about 200 examples, refreshed monthly) to validate the judge.

## Consequences
**Benefits:** full coverage, near-real-time quality metrics, and a scalable signal for fine-tuning data selection.

**Failure modes and detection**
| Failure mode | Detection |
|---|---|
| Self-preference bias (judge favors its own model family) | Use a judge from a different family than the generator; compare scores by generator model |
| Verbosity/position bias | Randomize order; track correlation of score with answer length |
| Judge drift after provider model updates | Pin judge version; rerun calibration set weekly and alert if agreement with humans drops > 5 points |
| Score inflation / low variance | Monitor score distribution; alert if stddev collapses |
| Hallucinated justifications | Require judge to cite supporting chunk spans; verify citations exist in context |
| Cost blowup | Sample (e.g. 20%) in high-traffic periods; budget alerts |

Human review remains for disagreements: low-confidence or borderline scores are queued for annotation.
