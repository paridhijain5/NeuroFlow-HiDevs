# ADR 001: Vector Store: pgvector over Pinecone, Weaviate, Qdrant

## Context
NeuroFlow needs vector similarity, keyword search, metadata filtering, and transactional links to documents, evaluations, and generations. Expected scale is up to a few million chunks. The team is small, so operational surface area matters. Candidates: pgvector, Pinecone (managed), Weaviate, Qdrant.

## Decision
Use **PostgreSQL with pgvector** (HNSW index, cosine distance).

## Consequences
**Positive**
- One datastore: chunks, vectors, tsvector keyword search, metadata, and evaluation scores live together, so hybrid search and filtering are one SQL query and no cross-system sync is needed.
- ACID: a document and its chunks commit atomically, so deletes cannot orphan vectors.
- Lower cost and simpler local dev (single Docker image); no vendor lock-in.

**Negative**
- Less specialized than Qdrant/Pinecone at 50M+ vectors; HNSW index builds and memory use need tuning.
- No built-in sharding; scaling needs read replicas or partitioning.

**Mitigation:** the retrieval layer sits behind a `VectorStore` interface, so Qdrant can replace pgvector if we exceed ~10M chunks or p95 search latency > 150 ms.
