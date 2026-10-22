# History HTTP adapter: local-development milestone

## Implemented

`D:\cc\chatbot\history_http.py` wraps HistoryService in a standard-library WSGI
application. No packages were added; FastAPI/Starlette/Uvicorn are not installed
in the inspected environment. This adapter is not a claim of completing the
planned FastAPI deployment. It separates HTTP handling from the existing service
so a later framework adapter can reuse the contracts.

Implemented routes:

| Method | Path | Behavior |
| --- | --- | --- |
| GET | /health/live | Process liveness only, no auth/DB/model calls |
| GET | /health/ready | Always 503 readiness_unverified until real probes exist |
| POST | /api/chat/conversations | Create with optional JSON title |
| GET | /api/chat/conversations | List with limit and optional before JSON cursor |
| GET | /api/chat/conversations/last-active | Last-active owned conversation |
| GET | /api/chat/conversations/{id} | Owned conversation metadata |
| GET | /api/chat/conversations/{id}/messages | Ascending recent page, limit/before_sequence |
| DELETE | /api/chat/conversations/{id} | Delete owned conversation |
| OPTIONS | History paths | Exact-origin preflight without authentication |

The supplied development launcher always constructs HistoryService(enabled=False).
With a valid verified account the history endpoints return 503 history_unavailable;
they do not read/write chat tables. Model/generation/message-submission endpoints
do not exist. This must not be presented as saved-chat support or a live chatbot.

## HTTP controls

- Bearer-header syntax validation; server-verified Owner remains the authorization source.
- Exact explicit origin list, no wildcard, no cookie credential support; CORS is not authorization.
- JSON object bodies only, 16 KiB body cap, bounded query/path length and field count,
  duplicate JSON/query key rejection, integer parsing, and rejection of non-finite JSON constants.
- POST requires Content-Length and JSON content type. Other methods reject bodies.
  Transfer/content encodings are rejected rather than implicitly decoded.
- No-store responses, nosniff, restrictive CSP, sanitized errors, no raw request logging
  in the development launcher, and no returned SQL/credentials/tracebacks.
- Process-local fixed-window remote-address rate limiting (default 60/minute), bounded
  address state, concurrency semaphore (default four service operations), Retry-After
  for busy/limited responses. Client X-Forwarded-For/Forwarded is never trusted here.

These controls do NOT replace production gateway limits. Rate state is per process;
shared NATs share a bucket. Health/preflight requests also count toward it. Production
must add distributed/account-level abuse controls and correct trusted proxy handling.
No total wall-clock deadline or response-size budget is implemented; backend socket/DB
timeouts do not replace them. Slow clients must be bounded by the deployment gateway.

WSGI may collapse raw duplicate headers before creating environ. The local request
handler explicitly rejects duplicate Authorization/Content-Length/Origin/Content-Type
through an adapter marker; other WSGI gateways must enforce duplicate header and
request-smuggling controls themselves. Do not assume the application can recover raw
header information in every deployment. The local server socket has a ten-second
operation timeout, not a full request deadline.

## Local launch only

```powershell
Set-Location 'D:\cc\chatbot'
& 'D:\cc\chatbot\.venv\Scripts\python.exe' 'D:\cc\chatbot\serve_history.py' --backend-origin http://127.0.0.1:3000 --allow-loopback-backend-http --frontend-origin http://localhost:5173 --port 8081
```

Supply the actual local backend port. The command does not start the backend or load
.env. It binds only 127.0.0.1, disables persistence, and needs no Groq key. Stop with
Ctrl+C. The HTTP server is synchronous and development-only; do not expose it publicly.
Python's wsgiref documentation explicitly warns it is not recommended for production.
Primary references reviewed: Python 3.13/3.14 wsgiref documentation and PEP 3333.

## Validation and remaining work

22 new adapter tests include route mapping, strict parsing/limits, CORS/preflight,
auth syntax, safe errors, rate/concurrency gates, fake-store HistoryService integration,
and real loopback HTTP including raw duplicate Authorization headers. The complete
suite is 138 passing tests (includes 10 cross-language auth tests and one adapter
loopback test); no real DB or Groq requests are made by that suite.

```powershell
& 'D:\cc\chatbot\.venv\Scripts\python.exe' -m unittest test_history_http test_auth_integration test_history_service test_conversation_store test_conversation_context test_query_plan test_document_context test_retrieval test_readiness test_books test_metadata_indexing -v
```

Persistence remains gated by deferred PostgreSQL integration tests, unapplied migration
003, and runtime permissions review. Real auth/deployment, production serving,
pooling, durable turn recovery, query execution, semantic routing/generation, and
frontend live connection remain pending. No frontend preview labels were removed.

## Indexing pause

The active resume_book_index.py worker was stopped when the user chose to continue
application work and update the Groq key later. A paused checkpoint was written in
ignored book-output/resume-status.json. No saved vectors/cache were deleted; no
database or key changes were performed. Resume explicitly after the replacement
key is configured; never start duplicate workers.