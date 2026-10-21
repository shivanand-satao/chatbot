CREATE TABLE IF NOT EXISTS chatbot.documents (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 filename text NOT NULL, title text NOT NULL, version_label text NOT NULL,
 file_hash text NOT NULL, page_count integer NOT NULL,
 status text NOT NULL DEFAULT 'extracting', extraction_report jsonb NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(filename,file_hash)
);
CREATE TABLE IF NOT EXISTS chatbot.document_chunks (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 document_id bigint NOT NULL REFERENCES chatbot.documents(id),
 source_key text NOT NULL, page_start integer NOT NULL, page_end integer NOT NULL,
 section_reference text NOT NULL DEFAULT '', chunk_type text NOT NULL,
 original_text text NOT NULL CHECK(length(btrim(original_text))>0),
 context_text text NOT NULL, source_details jsonb NOT NULL DEFAULT '{}',
 semantic_metadata jsonb, embedding_text text, embedding public.vector(384),
 embedding_model text, model_revision text, content_hash text NOT NULL DEFAULT '',
 embedded_content_hash text, token_count integer CHECK(token_count BETWEEN 1 AND 512),
 revision integer NOT NULL DEFAULT 1, manual_override boolean NOT NULL DEFAULT false,
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','ready','review','retired','failed')),
 updated_at timestamptz NOT NULL DEFAULT now(), embedded_at timestamptz,
 UNIQUE(document_id,source_key),
 CHECK(status <> 'ready' OR (embedding IS NOT NULL AND semantic_metadata IS NOT NULL
 AND embedding_text IS NOT NULL AND embedded_content_hash=content_hash))
);
CREATE TABLE IF NOT EXISTS chatbot.document_pages (
 document_id bigint NOT NULL REFERENCES chatbot.documents(id), page_number integer NOT NULL,
 extracted_text text NOT NULL, tables_json jsonb NOT NULL, quality_issues jsonb NOT NULL,
 PRIMARY KEY(document_id,page_number)
);
CREATE TABLE IF NOT EXISTS chatbot.document_chunk_history (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 chunk_id bigint NOT NULL REFERENCES chatbot.document_chunks(id),
 revision integer NOT NULL, snapshot jsonb NOT NULL, archived_at timestamptz NOT NULL DEFAULT now()
);
CREATE OR REPLACE FUNCTION chatbot.invalidate_document_chunk() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,chatbot AS $$
BEGIN
 IF TG_OP='INSERT' THEN
   NEW.content_hash := md5(jsonb_build_array(NEW.original_text,NEW.context_text,NEW.section_reference,NEW.source_details)::text);
 ELSIF (NEW.original_text,NEW.context_text,NEW.section_reference,NEW.source_details)
       IS DISTINCT FROM (OLD.original_text,OLD.context_text,OLD.section_reference,OLD.source_details) THEN
   INSERT INTO chatbot.document_chunk_history(chunk_id,revision,snapshot)
   VALUES(OLD.id,OLD.revision,to_jsonb(OLD));
   NEW.revision := OLD.revision+1;
   NEW.manual_override := true;
   NEW.content_hash := md5(jsonb_build_array(NEW.original_text,NEW.context_text,NEW.section_reference,NEW.source_details)::text);
   NEW.status := 'pending'; NEW.embedding := NULL; NEW.embedding_text := NULL;
   NEW.semantic_metadata := NULL; NEW.embedded_content_hash := NULL;
   NEW.token_count := NULL; NEW.embedded_at := NULL;
 END IF;
 NEW.updated_at := now();
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS invalidate_document_chunk ON chatbot.document_chunks;
CREATE TRIGGER invalidate_document_chunk BEFORE INSERT OR UPDATE ON chatbot.document_chunks
FOR EACH ROW EXECUTE FUNCTION chatbot.invalidate_document_chunk();
CREATE INDEX IF NOT EXISTS document_chunks_cosine_idx ON chatbot.document_chunks
USING hnsw(embedding public.vector_cosine_ops) WHERE status='ready';
CREATE INDEX IF NOT EXISTS document_chunks_status_idx ON chatbot.document_chunks(status,id);
CREATE OR REPLACE VIEW chatbot.ready_document_chunks WITH (security_invoker=true) AS
SELECT * FROM chatbot.document_chunks WHERE status='ready' AND content_hash=embedded_content_hash;
ALTER TABLE chatbot.documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE chatbot.document_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE chatbot.document_chunk_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE chatbot.document_pages ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON chatbot.documents,chatbot.document_chunks,chatbot.document_chunk_history,
 chatbot.document_pages,chatbot.ready_document_chunks FROM PUBLIC;