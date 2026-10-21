-- Applies only to the chatbot schema. Existing crime/authentication tables are unchanged.
-- pgvector has been verified to be installed in public on this database.
CREATE SCHEMA IF NOT EXISTS chatbot;
REVOKE ALL ON SCHEMA chatbot FROM PUBLIC;

CREATE TABLE IF NOT EXISTS chatbot.dataset_metadata_embeddings (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_schema TEXT NOT NULL CHECK (length(dataset_schema) > 0),
    dataset_table TEXT NOT NULL CHECK (length(dataset_table) > 0),
    metadata_type TEXT NOT NULL CHECK (metadata_type IN ('table_summary', 'column_group')),
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    original_text TEXT NOT NULL CHECK (length(btrim(original_text)) > 0),
    metadata_details JSONB NOT NULL,
    embedding public.vector(384) NOT NULL,
    embedding_model TEXT NOT NULL,
    model_revision TEXT NOT NULL,
    content_hash TEXT NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    token_count INTEGER NOT NULL CHECK (token_count BETWEEN 1 AND 512),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (dataset_schema, dataset_table, metadata_type, chunk_index)
);

ALTER TABLE chatbot.dataset_metadata_embeddings ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON chatbot.dataset_metadata_embeddings FROM PUBLIC;
CREATE INDEX IF NOT EXISTS dataset_metadata_embeddings_cosine_idx
    ON chatbot.dataset_metadata_embeddings USING hnsw (embedding public.vector_cosine_ops);