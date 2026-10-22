# Phase 1 readiness audit

## Deployed beta update — October 22, 2026

The audit below is historical. New live checks found all 10,348 document passages
searchable across six editions. Keyword source search and Groq structured query planning
are now implemented. The analytical runtime role was checked for access to only the
78 approved dataset tables, no account/history reads, and no source-table writes.
A live Groq plan compiled and executed against that restricted role, returning five
Maharashtra 2012 rows. This is not proof of statistical or current-law answerability.

History and generated legal answers remain disabled. Restricted runtime DSNs, not the
indexing connection, are configured on Render. Hosted backend/chatbot deployments and
frontend bundle were checked. Missing/invalid auth and CORS are smoke-tested separately;
the full browser flow still requires a real signed-in user session. See RENDER_DEPLOYMENT.md.

Audit completed October 22, 2026 (local date; machine report uses UTC).

## What ran

`audit_readiness.py` used the configured indexing database connection over required SSL, with an explicitly read-only transaction, statement/lock timeouts, and rollback. It made no model-provider calls, loaded no embedding model, ran no migrations, and changed no database rows or privileges.

Run again from this folder with `.venv\Scripts\python.exe audit_readiness.py`. The detailed local report is `readiness-output/report.json`, ignored by Git. A successful audit command means checks executed, not that deployment is approved. Consult its blockers.

## Live coverage findings

- Dataset metadata: 78 datasets, 434 vectors, zero invalid dimensions/norms, one model/revision, no missing indexed source tables, and no mismatches for indexed column names/types.
- These preliminary checks do not prove all source tables are indexed, detect newly added columns, establish units, or test numerical answers. Deeper schema/coverage validation remains part of retriever tests.
- Document model/revision matches dataset embeddings; 72 ready document vectors passed the source/vector consistency checks performed.
- Documents: 6 PDFs, 1,228 pages, 10,348 chunks; 72 ready and 10,276 pending.

| Uploaded source | Chunks | Ready | Pending | Pages with at least one ready passage |
| --- | ---: | ---: | ---: | ---: |
| Code of Criminal Procedure uploaded edition | 2,246 | 64 | 2,182 | 2 / 261 |
| Alphabetical Central Acts list (index only) | 139 | 8 | 131 | 2 / 19 |
| BNSS introduced bill edition | 1,930 | 0 | 1,930 | 0 / 297 |
| BNS introduced bill edition | 1,107 | 0 | 1,107 | 0 / 137 |
| Constitution edition as on 1 May 2024 | 3,189 | 0 | 3,189 | 0 / 402 |
| IPC uploaded edition | 1,737 | 0 | 1,737 | 0 / 112 |

Page coverage means at least one ready passage, not a fully searchable page. Edition labels are stored ingestion labels, not a new legal verification. Full document answering is blocked; ready-only retrieval can be built and tested with explicit limitations. Book enrichment remains stopped.

## Privilege findings and runtime gate

The audited indexing connection has BYPASSRLS, role/database creation privileges, table ownership/write access, and read access to authentication tables. It is unsuitable for the generated-SQL executor even when a request is placed in a read-only transaction.

Provision a dedicated analytical role before live generated-query execution. It must have only reviewed dataset reads and permitted retrieval operations, no authentication/session-table access, no ownership, and no elevated role attributes. A separate controlled service path will write conversation records. No privilege changes were made in this audit.

No `anon`/`authenticated` chatbot schema/table access was reported by the checked privilege queries. This is not a complete review of function execution grants, inherited roles, views, API exposure, or RLS behavior. Test the eventual runtime credentials and isolation policies directly.

## Existing authentication contract: source inspection

Inspected the existing backend's auth middleware, JWT utilities, auth routes, and login/refresh/logout/profile controller code without reading secret values.

- Requests use `Authorization: Bearer <access token>`.
- Access tokens are signed with `JWT_ACCESS_SECRET`; refresh tokens use a different configured secret.
- Payloads contain `id`, `username`, and `role`. The JWT helper supplies expiration via configuration; it does not explicitly configure algorithm, issuer, or audience.
- JWT library primary documentation states the signing algorithm defaults to HS256. Verify the actual deployment contract before configuring a Python verifier; pin the accepted algorithm rather than trusting a token header.
- Ordinary users are in `users` and log in with role `user`; administrators are in `admins`, using their stored role. Their numeric IDs must be treated as separate namespaces.
- `GET /api/auth/profile` verifies the access token and returns its decoded payload. It does not perform a fresh account-status lookup in the inspected handler.
- Logout removes the refresh-token record; the inspected access-token middleware does not consult a revocation store. Do not claim immediate server-side access-token revocation.

### Proposed initial chatbot authentication integration

Use the existing backend profile endpoint as a server-to-server identity verification boundary, with a configured HTTPS backend base URL, bounded timeout, fail-closed behavior, no token logging, and no user-controlled endpoint. This avoids copying the JWT signing secret into the chatbot service. No network authentication checks were made because no user token was supplied.

Validate the response schema and recognized roles. Derive history ownership as `(account_kind, account_id)`, mapping ordinary users to the user namespace and recognized admin-family roles to the admin namespace. Do not include a mutable admin role in the ownership key. Confirm actual permitted admin roles and status/revocation requirements before implementation. No administrator gains automatic access to another user's history.

Direct JWT verification is a possible later alternative after explicitly confirming key management, algorithm, expiry, issuer/audience compatibility, and revocation behavior. Do not silently require claims the existing issuer does not supply.

## Dependencies found in the current environment

Present: psycopg 3.3.6, python-dotenv 1.2.4, groq 1.7.0, pydantic 2.14.1, transformers 5.20.2, torch 2.14.1+cpu.

Not installed: FastAPI, Uvicorn, PyJWT, SQLGlot, psycopg-pool. No packages were installed. Choose and document runtime dependencies using primary documentation before implementation. These findings describe this environment, not production readiness.

## Validation

22 tests passed across `test_readiness`, `test_books`, and `test_metadata_indexing`. The new audit tests cover partial coverage, schema mismatches, elevated privileges, authentication-table access, model mismatch, application grants, and refusal to collect in a writable transaction.

The first live attempt failed its read-only guard. The audit was corrected to explicitly set transaction read-only and local timeouts before collecting evidence; the rerun passed that guard. Do not assume startup settings alone survive a connection intermediary.

## Next step

Phase 2: implement independently tested dataset-metadata and ready-document retrievers using the existing embedding helper, structured source metadata, model/revision filters, bounded results, and no generated SQL execution. Use the 72 ready document chunks only for limited retrieval checks; do not resume enrichment implicitly. Persistent conversation schema/API implementation follows the recorded plan after identity handling is verified.

## Primary reference checked

Auth0 node-jsonwebtoken README: https://github.com/auth0/node-jsonwebtoken/blob/master/README.md — default signing algorithm and verification configuration. The remaining findings above come from local source inspection and the read-only database audit, not external claims about the deployment.