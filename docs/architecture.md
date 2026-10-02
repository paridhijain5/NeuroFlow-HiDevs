# NeuroFlow Architecture

NeuroFlow has five subsystems sharing one Postgres (+pgvector) database, a Redis queue/cache, and an MLflow tracking server.

```mermaid
flowchart LR
  U[Client] --> API[FastAPI]
  API --> ING[Ingestion]
  API --> RET[Retrieval]
  RET --> GEN[Generation]
  GEN --> EVAL[Evaluation]
  EVAL --> FT[Fine-Tuning]
  FT -. better model .-> GEN
  ING --> PG[(Postgres + pgvector)]
  RET --> PG
  EVAL --> PG
  ING --> RD[(Redis)]
  EVAL --> RD
  FT --> ML[(MLflow)]
```

## 1. Ingestion Subsystem
Accepts PDF, DOCX, images, CSV, and web URLs; extracts content per modality, chunks, embeds, and writes to the vector store.

```mermaid
flowchart TD
  A[POST /ingest: file or URL] --> B[Validate type and size]
  B --> C[Store raw file, create document row: status=queued]
  C --> D[Enqueue job in Redis]
  D --> E{Modality router}
  E -->|PDF/DOCX| F[Text + table extraction]
  E -->|Image| G[Vision LLM caption + OCR]
  E -->|CSV| H[Row-group to text summarization]
  E -->|URL| I[Fetch, readability clean]
  F --> J[Chunker - see ADR 002]
  G --> J
  H --> J
  I --> J
  J --> K[Batch embed]
  K --> L[(INSERT chunks + vectors into pgvector)]
  L --> M[document status=ready: first queryable vector]
```

Key points: idempotent via content hash; workers are async and horizontally scalable; failures retry 3x with backoff then go to a dead-letter list; chunk metadata (doc_id, page, modality, source, created_at) enables filtering.

## 2. Retrieval Subsystem
Runs three searches in parallel, fuses with Reciprocal Rank Fusion (RRF), reranks with a cross-encoder, and returns a ranked context window.

```mermaid
flowchart TD
  Q[User query] --> QE[Embed query]
  Q --> KW[Query normalization]
  QE --> V[Vector similarity - pgvector HNSW, top 50]
  KW --> K[Keyword search - Postgres tsvector BM25-like, top 50]
  Q --> M[Metadata filter - doc ids, modality, date]
  M --> V
  M --> K
  V --> R[Reciprocal Rank Fusion: score = sum 1/(60+rank)]
  K --> R
  R --> X[Cross-encoder reranker, top 50 to top 8]
  X --> W[Context window assembler: dedupe, token budget]
  W --> OUT[Ranked context + citations]
```

Latency budget: parallel retrieval < 150 ms, rerank < 200 ms. Results cached in Redis by (query hash, filters).

## 3. Generation Subsystem
```mermaid
flowchart TD
  C[Ranked context] --> P[Prompt assembler: system + context + query + history]
  P --> RT[Model router - ADR 004: cost tier / capability / domain]
  RT --> LLM[LLM provider via abstraction layer]
  LLM --> S[Stream tokens over SSE]
  S --> CL[Client]
  LLM --> LG[Log full input/output pair, model, tokens, latency]
  LG --> PG[(generations table)]
  LG --> EQ[Enqueue evaluation job]
```

Provider failures trigger fallback to the next model in the tier. The stream endpoint reconnects using `Last-Event-ID`.

## 4. Evaluation Subsystem
Asynchronously scores every generation using an LLM judge (see ADR 003).

```mermaid
flowchart TD
  E[Eval job from Redis] --> L[Load query, context chunks, answer]
  L --> F[Faithfulness: claims grounded in context?]
  L --> AR[Answer relevance: addresses question?]
  L --> CP[Context precision: retrieved chunks actually used?]
  L --> CR[Context recall: relevant chunks retrieved?]
  F --> S[(evaluations table)]
  AR --> S
  CP --> S
  CR --> S
  S --> AG[Rolling aggregates: 1h / 24h / 7d, per model and pipeline]
  AG --> AL[Alert if below threshold]
```

## 5. Fine-Tuning Subsystem
```mermaid
flowchart TD
  EL[(Evaluation log)] --> SEL[Select pairs: faithfulness > 0.8 AND user rating >= 4]
  SEL --> FMT[Format as JSONL prompt/completion]
  FMT --> JOB[Submit fine-tuning job to provider]
  JOB --> TR[Track params + metrics in MLflow]
  TR --> CMP[Offline eval: tuned vs base on held-out queries]
  CMP -->|tuned wins| RTE[Register model; router sends similar queries to it]
  CMP -->|base wins| KEEP[Keep base, archive run]
```

Similarity routing: queries whose embedding is close to the fine-tuning cluster centroid (cosine > 0.8) go to the tuned model.

## Cross-cutting
- **Auth:** API keys (Bearer) with per-key rate limits.
- **Observability:** `/health`, `/metrics` (Prometheus), structured JSON logs with request IDs.
- **Storage:** Postgres (docs, chunks, vectors, generations, evaluations, jobs), Redis (queue, cache, rate limits), object/file store for raw uploads, MLflow for experiments.
