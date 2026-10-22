# Legal document search and explanations

The web UI searches every uploaded PDF without a document selector. Retrieval
remains keyword-based, not semantic search. Common spelling/inflection variants
are normalized, arrest questions require arrest-related text, and coverage is
ranked before density. Results are capped (three excerpts per document, ten total);
index-only editions are excluded from substantive evidence. Documents without matches are not
represented. The internal API retains its optional document filter for compatibility.

When the server-side `groq_api` credential is configured, the question and retrieved
public PDF excerpts are sent to Groq for a short, structured explanation. Model
timeout is ten seconds with no retries. The database connection closes before
generation. Questions, excerpts and explanations are not saved by this application;
this does not make a claim about provider-side retention.

The question and evidence are sent together. The answer has direct response,
analysis, conditional source-supported steps, limitations and clarification fields.
Each point selects a source and immutable evidence ID; the server supplies the
original quotation rather than asking the model to transcribe PDF text.
Invalid output, irrelevant/insufficient-source abstentions,
timeouts and provider failures fall back to the source passages. Quote validation
does not verify the interpretation, legal accuracy, relevance or current validity.
The UI leads with analysis and keeps editions/pages/text in collapsed references.
Provider failures, timeouts, invalid responses and insufficient evidence have distinct
statuses. Logs contain fixed failure categories only, not questions/provider messages.
The UI clearly warns about historical editions/bills and does not claim current law.

`GENERATION_ENABLED` continues to block unrestricted generated legal answers;
the bounded excerpt-explanation path is separate. No history or current-law answer
mode is enabled by this change.