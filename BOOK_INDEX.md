# Book index

Separate from the dataset index: `chatbot.documents`, `chatbot.document_pages`,
`chatbot.document_chunks`, `chatbot.document_chunk_history`, and the
`chatbot.ready_document_chunks` retrieval view. No application-user grants.

## Commands (PowerShell)

```powershell
& 'd:\cc\chatbot\.venv\Scripts\python.exe' 'd:\cc\chatbot\book_pipeline.py' extract
& 'd:\cc\chatbot\.venv\Scripts\python.exe' 'd:\cc\chatbot\book_pipeline.py' enrich --limit 100
& 'd:\cc\chatbot\.venv\Scripts\python.exe' 'd:\cc\chatbot\book_pipeline.py' report
& 'd:\cc\chatbot\.venv\Scripts\python.exe' 'd:\cc\chatbot\verify_books.py'
```

Omit `--limit` to process all pending chunks. Extraction and enrichment use a
shared advisory lock; do not run them simultaneously. Restart either command
after an interruption. Completed chunks and cached Groq responses are retained.
Enrichment makes billable Groq calls. `.env` needs `db_url` and `groq_api`.
Source defaults to `C:\Users\shiva\OneDrive\Desktop\books for rah`.

## Editing a passage

```sql
SELECT id,document_id,page_start,section_reference,original_text,
       semantic_metadata,embedding_text,status,revision
FROM chatbot.document_chunks ORDER BY id;

-- Replace 123 with an actual chunk ID. Use parameters in application code.
UPDATE chatbot.document_chunks
SET original_text = 'Your corrected passage'
WHERE id = 123;
```

The trigger archives the complete previous row, marks a manual override,
increments revision, invalidates semantic metadata/vector and marks it pending.
Rebuild only that row:

```powershell
& 'd:\cc\chatbot\.venv\Scripts\python.exe' 'd:\cc\chatbot\book_pipeline.py' enrich --chunk-id 123
```

If the edited passage exceeds input capacity it becomes `review`, not truncated.
Split oversized manual passages into appropriately contextualized rows, retire
the replaced row, then mark approved new rows pending. Automatic resplitting of
manual edits is not implemented. Source re-ingestion uses DO NOTHING on stable
source keys, never overwriting manual edits. New PDFs with changed file hashes
create separate document versions; retire obsolete versions explicitly.

Always retrieve through `ready_document_chunks`, with matching embedding
model/revision and desired document version. A ready vector is generated from
`'passage: ' || embedding_text`. The exact source text is included. Semantic
summary/topics/questions are stored in JSON; if the full summary will not fit,
only topics are included alongside source text in the embedding input.

## Extraction limitations

The full page text and extracted table grids are retained in `document_pages`.
Chunks are conservatively split at detected section headings and token limits;
page boundaries remain citation boundaries. Tables retain header context and
rows where they fit. Oversized rows may split into repeated-header segments.
Exact-header continuation is recorded, not automatically merged across pages.
Repeated headers/footers are retained rather than risk deleting legal text.
Font/CID/replacement-character and sparse-page checks are heuristics, not OCR
validation; legacy-font Hindi may still need manual review. OCR is not installed.
No guarantee of legal completeness or current-law applicability is made.

All six uploaded documents retain edition labels, including introduced bills
and the Central Acts index. Generated prose is AI-inferred, not authoritative.
`review` entries are excluded until corrected and explicitly set to pending.

`d:\cc\chatbot\book-output\report.json` contains the latest counts; it is only
updated when a command finishes. During extraction, query the database for live
progress. No FastAPI endpoint or frontend is implemented by this pipeline.