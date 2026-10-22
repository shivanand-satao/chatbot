# Legal document search and explanations

The web UI searches every uploaded PDF without a document selector. Retrieval
remains keyword-based, not semantic search. Results are diversified (at most two
excerpts per document, eight total); documents without matching keywords are not
represented. The internal API retains its optional document filter for compatibility.

When the server-side `groq_api` credential is configured, the question and retrieved
public PDF excerpts are sent to Groq for a short, structured explanation. Model
timeout is eight seconds with no retries. The database connection closes before
generation. Questions, excerpts and explanations are not saved by this application;
this does not make a claim about provider-side retention.

Each explanation point must cite a returned source ID and include an exact quote
from that excerpt. Invalid output, irrelevant/insufficient-source abstentions,
timeouts and provider failures fall back to the source passages. Quote validation
does not verify the interpretation, legal accuracy, relevance or current validity.
The UI clearly warns about historical editions/bills and does not claim current law.

`GENERATION_ENABLED` continues to block unrestricted generated legal answers;
the bounded excerpt-explanation path is separate. No history or current-law answer
mode is enabled by this change.