# Retrieval baseline — no Groq required

## Available now

`retrieval.py` retrieves candidate dataset metadata or ready document passages with the cached local E5 model. It makes no Groq calls, generates no SQL, queries no individual crime records, and generates no final answer. Retrieval output is evidence for later planning/generation stages, not an assertion that the question is answerable.

`search_evidence.py` provides a local command-line interface. It is not a public API and must not be exposed using the elevated indexing credential. No user input is interpolated into executable SQL; database searches use fixed statements and bound parameters.

The current small-catalog baseline computes exact cosine similarities, combines semantic ranks with a simple Unicode word-overlap rank using reciprocal-rank fusion, and groups dataset chunks so each returned dataset is distinct. Ranking scores are not calibrated answer confidence. Parallel similarly named tables remain separate candidates.

Dataset results include the best matched metadata chunk and its structured metadata details. Document results preserve original text, document/version identity, PDF page boundaries, chunk revision/hash, and stable source identifiers. Filters support document ID, exact edition label, and exact stored section reference. Unscoped document searches are discovery only, not permission to merge different editions into a legal answer.

Searches require the stored embedding model and revision to match the query encoder. Document retrieval uses the existing ready view, which excludes pending and source-invalidated chunks. Ready coverage is reported per document before search. A document with no compatible ready passages returns `no_ready_evidence` without embedding the question.

## Run locally

From `D:\cc\chatbot` in PowerShell:

```powershell
$env:PYTHONIOENCODING = 'utf-8'
& 'D:\cc\chatbot\.venv\Scripts\python.exe' 'D:\cc\chatbot\search_evidence.py' datasets 'auto theft motor vehicles'
& 'D:\cc\chatbot\.venv\Scripts\python.exe' 'D:\cc\chatbot\search_evidence.py' datasets 'वाहन चोरी'
& 'D:\cc\chatbot\.venv\Scripts\python.exe' 'D:\cc\chatbot\search_evidence.py' documents 'criminal procedure' --document-id 1
& 'D:\cc\chatbot\.venv\Scripts\python.exe' 'D:\cc\chatbot\search_evidence.py' documents 'constitutional safeguards' --document-id 5
```

The fourth command should currently report no ready evidence, not a constitutional answer. Confirm document IDs against coverage output if the collection changes.

Only the existing `.env` `db_url` is read. No Groq key is needed for retrieval. The model loads from the local cache only; no model download or indexing is triggered. Connections use required SSL and an explicitly read-only transaction, bounded statement/lock timeouts, and rollback. Generic error messages deliberately omit driver details that might contain secrets.

## Bounds and limitations

- Maximum question length: 2,000 characters, subject also to the encoder's 512-token maximum; overlong tokenized inputs fail rather than truncate.
- Results: 1–10. Candidate scan budget: 2,000 compatible rows per pipeline; a larger result fails closed instead of silently searching an incomplete prefix. Larger catalogs will need indexed candidate generation and recall evaluation.
- Exact small-catalog scans favor correctness of candidate ordering over large-scale performance. No schema/index migrations were made.
- Lexical ranking is a basic word-overlap baseline, not a morphological full-text engine or learned reranker. Adjacent-chunk expansion and context-budget assembly remain later work.
- There is no calibrated relevance/answerability cutoff. Unrelated questions can return weak candidates; the later answerability gate must reject unsupported questions rather than treating any nearest neighbor as proof.
- Current document evidence is extremely limited: 72 ready / 10,348 total chunks at the last audit. Four documents have no ready chunks. Pending content is never presented as searched.
- No LLM router, SQL planner/executor, conversation persistence, FastAPI endpoint, or frontend integration has been added by this stage.

## Validation performed

```powershell
& 'D:\cc\chatbot\.venv\Scripts\python.exe' -m unittest test_retrieval test_readiness test_books test_metadata_indexing -v
& 'D:\cc\chatbot\.venv\Scripts\python.exe' 'D:\cc\chatbot\verify_retrieval.py'
```

31 offline unit tests passed. Six live checks passed using the database and cached local model:

1. Auto-theft dataset discovery.
2. Serious-fraud dataset discovery.
3. Stolen/recovered-property dataset discovery.
4. Hindi auto-theft dataset discovery.
5. Empty ready-evidence response for an unindexed document.
6. Ready-passage self-retrieval with explicit document/section filtering.

The last check is a retrieval wiring smoke test, not independent legal QA evaluation. The suite is small and does not establish general retrieval quality, legal applicability, SQL correctness, or production readiness. Expand evaluation before live answer generation. Detailed live results are written to Git-ignored `retrieval-output/verification.json`.

## Next stages

### Document context and citation baseline

`document_context.py` now assembles complete retrieved passages within a configurable character budget (default 12,000). It preserves source text, PDF page references, edition labels, chunk revision/hash, and source IDs. Duplicate source IDs are removed. Oversized passages are omitted, never silently truncated. Candidates spanning documents/editions require explicit document selection rather than being silently combined.

Use the CLI with `--context` in documents mode:

```powershell
& 'D:\cc\chatbot\.venv\Scripts\python.exe' 'D:\cc\chatbot\search_evidence.py' documents 'criminal procedure' --document-id 1 --context --context-max-chars 12000
```

Possible context statuses are `candidate_context`, `no_ready_evidence`, `document_selection_required`, and `context_budget_exceeded`. Partial coverage and source limitations are retained. A candidate context is not an answerability approval. Adjacent-chunk expansion is not implemented.

The future generator response contract is an object containing only `claims`, each with `text` and `source_ids`. `validate_cited_response` rejects missing, duplicate, and unknown citation IDs and attaches metadata from assembled sources, not model-provided page/title fields. It validates citation identity only: it does not prove that the cited passage supports the claim, block every prompt-injection attempt, or verify current law.

Context is JSON-encoded source data, not trusted instructions. Character bounds are not a substitute for the eventual generation model's token budget; implement model-aware prompt budgeting when generation is connected.

After this extension, 42 offline unit tests and 8 live retrieval/context/citation wiring checks passed with zero Groq calls. The additional live citation test uses a source quote, not a generated answer. No end-to-end answer quality claim is made.

Build safe query planning/execution only with the restricted runtime-access gate satisfied. Build document context assembly and citation checks against ready evidence. Add authenticated conversation storage and follow-up handling according to `IMPLEMENTATION_PLAN.md`. Groq-dependent generation and enrichment can wait until the user restores credits; do not silently restart either.