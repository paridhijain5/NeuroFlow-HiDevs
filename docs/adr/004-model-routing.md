# ADR 004: Model Routing

## Context
Sending every query to the strongest model is costly; sending all to the cheapest hurts quality. Routing decisions depend on cost, latency, capability, and domain.

## Decision
Route with a rule-based classifier (upgradeable to a learned router) over three tiers.

| Query type | Signals | Tier | Rationale |
|---|---|---|---|
| Simple factual lookup | short query, top rerank score > 0.85, single chunk answers | Small/cheap | Context does the work |
| Summarization / multi-doc synthesis | many chunks, "summarize/compare" intent | Mid | Needs long-context coherence |
| Multi-step reasoning, math, code | reasoning keywords, low retrieval confidence | Large | Capability dominates cost |
| Image-containing context | modality = image | Vision-capable | Required capability |
| Domain with fine-tuned model | query embedding cosine > 0.8 to tuned cluster, tuned model beat base offline | Fine-tuned | Proven win |
| Latency-critical (pipeline flag) | `max_latency_ms` set | Fastest available | SLA |

Fallback: on provider error or timeout, escalate to the next provider in the same tier, then the next tier up.

## Consequences
- Expected cost reduction of 40-60% vs. always-large (to be verified by the evaluation subsystem per tier).
- Routing decisions are logged on each generation so misroutes can be analyzed.
- This ADR is the implementation spec for Task 38.
