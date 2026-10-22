# Public Render bootstrap

Build: pip install -r requirements-runtime.txt

Start: gunicorn 'render_app:create_app()' --bind 0.0.0.0:$PORT --workers 1 --threads 4 --worker-class gthread --timeout 30 --limit-request-line 2048 --limit-request-fields 40 --limit-request-field_size 8190

Plan: free. Region: Singapore. No disk or new database.

Set BACKEND_ORIGIN to the existing HTTPS backend origin. All feature flags default
to false. GET /health/live is public liveness only, not database/auth readiness.
Without CHATBOT_SERVICE_SECRET all other routes fail closed with 401. Once a
strong random service secret is configured, the backend must send it in
X-Chatbot-Service-Secret and forward the user's Authorization bearer token.
ProfileVerifier independently checks that token against the fixed backend origin.
User IDs/roles in client headers are never accepted as identity.

History requires explicit HISTORY_ENABLED=true and restricted history_db_url.
Do not enable it until secret rotation and backend gateway checks are complete.
RETRIEVAL_ENABLED=true enables keyword source candidates with restricted retrieval_db_url.
ANALYTICS_ENABLED=true enables Groq query planning with restricted analytics_db_url and
server-only groq_api. GROQ_MODEL defaults to openai/gpt-oss-120b. Model plans are validated
and compiled to parameterized SQL. Database rows are not sent to Groq. Users must select
one approved dataset and review query semantics. GENERATION_ENABLED=true is still rejected:
verified current-law legal answers are not implemented.
No local .env is loaded and no database is contacted during bootstrap startup.
The broader environment template includes future optional configuration, not a
claim that those features are deployed. Do not upload privileged indexing secrets.

No browser CORS origins are allowed: frontend calls backend, backend calls chatbot.
Rate limits are process-local, not distributed/user quotas. Gunicorn timeout is
not a full per-request deadline for threaded workers. Before enabling persistence,
validate gateway deadlines, duplicate-header rejection, user quotas, service-secret
handling and live owner isolation. The bootstrap is not a production-ready legal
answering system. Free instances may sleep and share workspace usage allowances.

## Runtime limits

- /api/chat/documents and /search expose authenticated keyword source candidates.
- /api/chat/datasets and /ask expose approved datasets and validated query execution.
- /ask accepts only question/table, never SQL, owner IDs or a client catalog.
- Runtime roles cannot read accounts/history or write source records.
- Queries check live column types, use read-only transactions and 5-second SQL timeouts,
  display at most 100 rows and bound response size. Groq timeout is 8 seconds, no retries.
- Query validity is not statistical answerability. Units, coverage, total/component overlap
  and AI-selected filters require review. Cross-table joins are unsupported.
- Startup never provisions roles/policies/indexes. Administrative provisioners are one-time
  explicit tools; inspect partial failures before retry. Never upload db_url or local .env.
- History remains disabled pending exposed-password rotation. Questions/results are not saved.