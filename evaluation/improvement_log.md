# Improvement Log — Quality Sprint (Task 18)

Baseline: `evaluation/quality_baseline.json` · Final: `evaluation/quality_final.json`
Fill in **real numbers from your own runs** — this log is evaluated alongside the code.
Each improvement is controlled by an env var (see `pipelines/quality_params.py`).

## 1. Parent-child chunking  (`PARENT_CHILD_ENABLED`, `CHILD_CHUNK_TOKENS`)
**What I changed:** Embedded 128-token child chunks and returned the 512-token parent for generation context (`backend/ingestion/parent_child.py`).
**Why I expected it to help:** Small chunks match queries more precisely; parents restore the surrounding context the LLM needs, which should lift hit rate and faithfulness.
**Before → after:** Hit@10 `__ → __`, MRR@10 `__ → __`, Faithfulness `__ → __`, Context precision `__ → __`
**Decision:** keep / revert — because `__`.

## 2. Weighted RRF  (`DENSE_WEIGHT`, `SPARSE_WEIGHT`, `RRF_K`)
**What I changed:** Replaced equal-weight RRF with weighted RRF (dense 0.6 / sparse 0.4 for technical domains) via `backend/retrieval/weighted_fusion.py`.
**Why I expected it to help:** Technical queries benefit from semantic matching more than lexical overlap.
**Before → after:** MRR@10 `__ → __`, Hit@10 `__ → __`
**Decision:** keep / revert — because `__`.

## 3. Query result caching  (`QUERY_CACHE_ENABLED`, `QUERY_CACHE_TTL_SECONDS`)
**What I changed:** Redis cache of full responses keyed by normalised query + config fingerprint, 30-minute TTL (`backend/cache/query_cache.py`).
**Why I expected it to help:** Repeated queries skip retrieval + generation entirely, cutting P95 latency.
**Before → after:** P95 latency `__s → __s` (note: cache hit rate during eval `__%`; the golden set has few repeats, so measure with repeated queries too)
**Decision:** keep / revert — because `__`.

## 4. Prompt A/B test  (`PROMPT_VARIANT`)
**What I changed:** Compared system-prompt variants A and B with `evaluation/prompt_ab_test.py`; results are in MLflow experiment `prompt_ab_test`.
**Why I expected it to help:** `__`
**Before → after:** Faithfulness `__ → __`, Answer relevance `__ → __`, Overall `__ → __`
**Decision:** keep variant `__` — because `__`.

## 5. HNSW `ef_search` tuning  (`HNSW_EF_SEARCH`)
**What I changed:** Swept `hnsw.ef_search` over `__` and chose `__` for production query load.
**Why I expected it to help:** Higher ef_search improves recall at some latency cost.
**Before → after:** Hit@10 `__ → __`, P95 latency `__s → __s`
**Decision:** keep / revert — because `__`.

## Final results
| Metric | Target | Baseline | Final |
|---|---|---|---|
| Retrieval Hit Rate@10 | > 0.80 | | |
| Retrieval MRR@10 | > 0.60 | | |
| Faithfulness | > 0.78 | | |
| Answer Relevance | > 0.75 | | |
| Context Precision | > 0.72 | | |
| Overall Eval Score | > 0.75 | | |
| P95 Query Latency | < 4 s | | |
