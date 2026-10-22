# CPU source-only indexing

The remaining pending document chunks now use original passages plus existing
document context, without Groq generation. Existing ready vectors and semantic
cache files are preserved. No schema changes or source edits are required.

```powershell
& 'D:\cc\chatbot\.venv\Scripts\python.exe' 'D:\cc\chatbot\resume_book_index.py' --strategy source-only --batch-size 8
```

Do not start a second worker. The existing advisory lock is retained. The default
strategy remains enriched for backward compatibility; always pass source-only
explicitly for CPU indexing without API calls.

The local cached multilingual E5 small model stays pinned to its cached revision,
on CPU with two compute threads. Inference batches contain eight passages; bounded
write transactions contain up to 32 chunks. No downloads or GPU use are enabled.
Source text is never truncated. Inputs above 500 tokens are marked review.
Writes require matching id, revision, hash, and pending status; ready rows are
never overwritten. Batches are resumable after interruption.

semantic_metadata records embedding_strategy=source-only-v1 and
enrichment=not_generated, with generator_model=null. This is a provenance marker,
not generated semantic metadata. Older enriched vectors remain in the same model
space; comparative retrieval quality has not been evaluated.

The progress monitor reads live DB counts every five seconds:

```powershell
& 'D:\cc\chatbot\.venv\Scripts\python.exe' 'D:\cc\chatbot\watch_book_index.py' --interval 5
```

Initial live check on October 22, 2026: 224 new vectors saved in approximately
16 seconds after model initialization (about 14 chunks/second in that interval).
This is an observed short interval, not a guaranteed throughput or completion ETA.
At the next read-only validation, all 404 retained enriched and 384 source-only
ready vectors passed dimension, normalization, source-preservation, token-count,
and content-hash checks. This does not verify factual correctness or retrieval quality.

Validation: 149 offline/loopback tests passed, including seven new source-only
tests. No production database mutation tests were run; the operational writes are
the user-authorized pending-chunk indexing updates only.