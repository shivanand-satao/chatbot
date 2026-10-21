# Identity and history service contract (not a deployed API)

## Implemented boundary

`server_identity.py` verifies a bearer header by calling the server-configured backend origin at `/api/auth/profile`. It never locally decodes JWT claims, shares JWT signing secrets, or accepts a client-provided verification URL. HTTPS is required except explicitly enabled loopback HTTP for development. It does not follow redirects, discover environment proxies, cache identities, or retry failed requests. Responses are capped at 16 KiB with a maximum configured socket timeout of ten seconds. This socket timeout is not a total end-to-end deadline; a future HTTP deployment must also bound total request duration and concurrent authentication work.

Malformed headers and backend 401/403 responses reject authentication. Network errors, redirects, oversized bodies, invalid JSON, and unexpected response shapes fail closed as identity-service failures. Token/response/transport details are not returned in errors. Never log authorization headers at the adapter, proxy, or application layer.

Verified roles `user` map to the user namespace; `admin`, `superadmin`, and `moderator` map to the admin namespace. Unknown roles are rejected. Account IDs accept positive safe JSON integers or canonical positive decimal strings within PostgreSQL bigint range. Request-supplied account IDs/roles are never used to choose ownership.

## Backend source inspection and limitations

Inspected locally:

- `D:\cc\backend\backend\src\middleware\authMiddleware.js`
- `D:\cc\backend\backend\src\controllers\auth\authController.js`
- `D:\cc\backend\backend\src\routes\authRoutes.js`
- `D:\cc\backend\backend\src\middleware\adminMiddleware.js`
- `D:\cc\backend\backend\src\app.js`

The profile route verifies the access token and returns `{success: true, user: decodedClaims}`. It does NOT reload account existence, disabled status, current role, or token revocation from storage. Logout removes a refresh token, not already-issued access tokens. Therefore this boundary inherits access-token lifetime and revocation limitations; it must not be described as current account-status verification. Live user/admin profile responses, bigint representation, deployed backend behavior, and actual role provenance remain unverified. In particular, confirm that admin-table roles can never equal `user`, since the token lacks a separate account-kind claim. This is an integration gate, not grounds for guessing an owner namespace.

## History service

`history_service.py` is a framework-neutral handler, not a router, listener, or FastAPI app. Storage is disabled by default. Enabling it requires an explicitly supplied owner-scoped repository context factory and verifier; no environment credentials are loaded. Authentication is checked on every call, before opening a storage scope. Each storage scope must yield a fresh `ConversationStore` with the same verified owner and close/release its connection afterward. Do not share request-bound stores across accounts.

Proposed HTTP adapter mapping (not yet registered):

| Method / path | Action | Input |
| --- | --- | --- |
| POST `/api/chat/conversations` | create | optional title |
| GET `/api/chat/conversations` | list | limit, optional before cursor |
| GET `/api/chat/conversations/last-active` | last_active | none |
| GET `/api/chat/conversations/{id}` | get | conversation_id |
| GET `/api/chat/conversations/{id}/messages` | messages | conversation_id, limit, optional before_sequence |
| DELETE `/api/chat/conversations/{id}` | delete | conversation_id |

The future adapter selects the action itself; clients do not choose arbitrary store methods. It parses bounded query values to typed integers/objects before calling the handler. Unknown fields, ownership claims, assistant contents, noncanonical UUID strings, invalid limits, and timezone-free cursors are rejected. The handler does not accept client-supplied assistant answers, start turns, invoke models, or complete generation requests. Message submission remains unavailable until durable pending-turn recovery is designed.

Successful results use `{success: true, data: ...}` with UUID/timestamp strings and allowlisted public fields only. Errors use `{success: false, error: {code: ...}}`: 400 invalid request, 401 authentication required, 404 unknown action or unavailable conversation, 409 conflict, 503 disabled history/identity or database failure. Unexpected programming errors are deliberately not swallowed; the future adapter must sanitize 500 responses and log safely. Permission errors do not distinguish missing from foreign-owned conversations.

Listing returns `conversations` plus `next_before` (`created_at`, `id`). Message pages return ascending `messages` plus `next_before_sequence`. Cursors are exclusive; a full page supplies a cursor even if the following page is empty. Empty pages return a null cursor. Creation-order listing and last-active lookup are distinct operations. Message rows preserve failed responses and source metadata; no incomplete records are silently filtered. Ownership fields are never part of conversation response objects.

## Deployment gates

No database migration was applied, no real tokens were used, and no backend profile call was made during this milestone. Persistence is not connected to the running app. Before enabling it:

1. Resolve namespace/account-status limitations and verify real user/admin tokens server-to-server without logging secrets.
2. Run isolated PostgreSQL migration, privilege, locking, concurrency, and deletion-race tests; review least-privilege role policies before applying migration 003.
3. Add an HTTP adapter with strict body/header/query limits, duplicate Authorization rejection, safe 500 responses, no-store cache headers, explicit CORS origins, rate/concurrency limits, and total request deadlines. Blocking verifier/database operations must not run directly on an async event loop.
4. Add durable worker recovery before exposing message submission; verify retry/deletion/account-switch behavior end to end.
5. Integrate frontend history and login/logout lifecycle only after these contracts are verified live.

Primary documentation reviewed: Python 3.13 `http.client` (HTTPSConnection, socket timeout, bounded response reads) and PostgreSQL row-security documentation (default deny and owner/BYPASSRLS exceptions). Neither documentation review nor offline mocks establishes deployed security behavior.

## Offline validation

Run from `D:\cc\chatbot`:

```powershell
& 'D:\cc\chatbot\.venv\Scripts\python.exe' -m unittest test_history_service test_conversation_store test_conversation_context test_query_plan test_document_context test_retrieval test_readiness test_books test_metadata_indexing -v
```

All transport/database behavior in the new boundary tests is mocked. No Docker, Groq, database, or backend connection is required.