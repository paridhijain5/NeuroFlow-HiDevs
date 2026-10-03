-- GIN index for metadata containment (@>) used by metadata-filtered retrieval.
CREATE INDEX IF NOT EXISTS chunks_metadata_gin ON chunks USING gin (metadata jsonb_path_ops);
