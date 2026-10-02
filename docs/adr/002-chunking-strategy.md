# ADR 002: Chunking Strategy

## Context
Chunking determines retrieval quality. Options: **fixed-size** (e.g. 512 tokens, overlap) is fast and predictable but cuts sentences and ideas; **sentence-boundary** keeps sentences intact and is cheap, but chunk sizes vary and topics can be split; **semantic** splits where embedding similarity between adjacent sentences drops, giving coherent chunks at the cost of extra embedding calls and slower ingestion.

## Decision
Default to **sentence-boundary chunking** (target 400 tokens, max 512, 64-token overlap, never splitting tables or code blocks). Switch to **semantic chunking** per pipeline when: documents are long and topic-shifting (reports, transcripts, books); evaluation shows context precision < 0.65 for that pipeline; or ingestion latency is not a constraint. Use **fixed-size** only for unstructured text with no reliable sentence boundaries (logs, OCR noise).

## Consequences
- Good coherence at low ingestion cost for most documents.
- Chunker is a pipeline config field, so strategies can be A/B tested using the evaluation subsystem.
- Semantic chunking costs roughly 2-3x ingestion embedding calls; it is opt-in.
- Changing strategy requires re-ingestion; chunks store `chunker_version` in metadata.
