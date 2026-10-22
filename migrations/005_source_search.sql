-- Additive keyword-search access. Never grants history/authentication access.
CREATE ROLE chatbot_source_search NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOINHERIT NOREPLICATION NOBYPASSRLS;
GRANT USAGE ON SCHEMA chatbot TO chatbot_source_search;
GRANT SELECT (id,title,filename,version_label,page_count,status)
    ON chatbot.documents TO chatbot_source_search;
GRANT SELECT (id,document_id,page_start,page_end,section_reference,original_text,
    status,content_hash,embedded_content_hash,revision)
    ON chatbot.document_chunks TO chatbot_source_search;
CREATE POLICY source_search_documents ON chatbot.documents FOR SELECT
    TO chatbot_source_search USING (true);
CREATE POLICY source_search_chunks ON chatbot.document_chunks FOR SELECT
    TO chatbot_source_search USING (status='ready' AND content_hash=embedded_content_hash);
CREATE INDEX document_chunks_keyword_idx ON chatbot.document_chunks
    USING gin (to_tsvector('simple'::regconfig, original_text))
    WHERE status='ready' AND content_hash=embedded_content_hash;
ALTER ROLE chatbot_source_search SET default_transaction_read_only=on;
ALTER ROLE chatbot_source_search SET statement_timeout='8s';
ALTER ROLE chatbot_source_search SET lock_timeout='2s';
ALTER ROLE chatbot_source_search SET search_path=pg_catalog,chatbot;