# Dataset metadata index

This step creates only `chatbot.dataset_metadata_embeddings` in the existing
Supabase database. It does not index individual records or PDFs and does not
create chatbot APIs. The original datasets/authentication tables are unchanged.

## Run

Use the existing `db_url` in the adjacent `.env`:

```powershell
& 'd:\cc\chatbot\.venv\Scripts\python.exe' 'd:\cc\chatbot\index_dataset_metadata.py'
& 'd:\cc\chatbot\.venv\Scripts\python.exe' 'd:\cc\chatbot\test_metadata_indexing.py'
& 'd:\cc\chatbot\.venv\Scripts\python.exe' 'd:\cc\chatbot\verify_metadata_index.py'
```

Requires existing connection-test/benchmark dependencies and the already
downloaded `intfloat/multilingual-e5-small` model cache. The indexer loads only
local model files, records their revision, and uses CPU batches of two.

## Stored information

- Source schema/table, metadata type, and chunk number.
- Exact readable metadata text plus structured JSON details.
- Actual columns/types/comments, exact row count at indexing time, bounded
  dimension values, actual integer-year range, and declared foreign keys.
- Normalized `vector(384)`, model/revision, SHA-256 hash, token count, timestamps.

Table summaries and complete column metadata are split into inputs of at most
480 tokens including model prefix/special tokens; input is never truncated.
Every chunk includes its source table. Large structured metadata lines may be
split across chunks; complete structured details remain available in JSONB.

Units and meanings without documentation remain explicitly unverified. Values
may include aggregate areas/categories, and parallel dataset tables stay separate.
Groq `openai/gpt-oss-120b` now generates explicitly AI-inferred categories,
summaries, granularity, exact-column descriptions, example questions, and
uncertainties. Its input includes actual schema/comments/statistics and up to
five dimension samples per field; never full records. JSON shape and exact
column coverage are validated, but prose is not thereby proven factually correct.
Generated prose must not override verified schema/data or establish metric units.
Validated responses are cached by evidence/model/prompt hash. Failed enrichment
does not silently fall back or replace the existing table entries. Changed text
and freshly generated E5 vectors are committed together per source table.

Dimension enumeration is
limited to 40 values per field and marked when truncated; individual records
are never embedded. Statistics describe the indexing snapshot, not live results.

## Incremental behavior

Unchanged content/model/revision is skipped. Changed entries are upserted, and
obsolete chunks for each currently discovered table are removed transactionally.
Completed tables survive interrupted runs. An advisory lock prevents two copies
of this script from indexing simultaneously. No automatic startup indexing.

Dropped/renamed source tables are not automatically removed: coverage validation
will fail if stale dataset entries remain, requiring explicit review/removal.

## Inspect in Supabase SQL Editor

```sql
SELECT dataset_table, metadata_type, chunk_index, original_text,
       metadata_details, embedding_model, model_revision, token_count,
       public.vector_dims(embedding) AS dimensions
FROM chatbot.dataset_metadata_embeddings
ORDER BY dataset_table, metadata_type, chunk_index;

SELECT count(*) AS entries, count(DISTINCT dataset_table) AS datasets
FROM chatbot.dataset_metadata_embeddings;
```

An HNSW cosine index is created. RLS is enabled with no application-user policies;
PUBLIC, anon, and authenticated access is not granted. Inspection/indexing needs
the privileged database connection. The schema is separate from `public`, so
the existing backend's public-table dataset listing does not include this table.

The latest local counts and three basic vector-retrieval checks are written to
`d:\cc\chatbot\metadata-output\last_index_report.json` (Git-ignored). These
checks verify table discovery, not SQL generation or numerical answer accuracy.

## Semantic exports

`d:\cc\chatbot\metadata-output\crime_schema_summary.json` stores structured
documents with verified details and separately labeled inferred metadata.
`d:\cc\chatbot\metadata-output\crime_schema_summary.md` stores readable summaries
and column descriptions. Exports checkpoint after each successfully indexed table;
an interrupted run may leave partial exports. Rerun to complete them.
`groq_api` in `.env` is required in addition to `db_url`. API calls can incur
usage charges. Unchanged evidence reuses validated cached responses; no extra
embedding provider credentials are required.