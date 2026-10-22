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
Retrieval/generation flags true are rejected: those HTTP APIs are not implemented.
No local .env is loaded and no database is contacted during bootstrap startup.
The broader environment template includes future optional configuration, not a
claim that those features are deployed. Do not upload privileged indexing secrets.

No browser CORS origins are allowed: frontend calls backend, backend calls chatbot.
Rate limits are process-local, not distributed/user quotas. Gunicorn timeout is
not a full per-request deadline for threaded workers. Before enabling persistence,
validate gateway deadlines, duplicate-header rejection, user quotas, service-secret
handling and live owner isolation. The bootstrap is not a production-ready legal
answering system. Free instances may sleep and share workspace usage allowances.