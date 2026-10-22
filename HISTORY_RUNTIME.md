# History database milestone — October 22, 2026

Applied explicitly authorized migrations 003 and 004 to the configured Supabase
database after a successful rollback rehearsal of both migrations. Three history
tables now exist; existing application/document tables were not migrated or edited.

Dedicated login: chatbot_history_runtime. It has no elevated role attributes or
memberships, no ownership, and SELECT/INSERT/UPDATE/DELETE grants only on the three
history tables. RLS is enabled and forced; anon/authenticated/service_role have no
history-table grants. No policies were granted to browser roles. The new credential
is stored in ignored D:\cc\chatbot\.env as history_db_url; it was not printed.
Do not copy this server secret into frontend configuration or source control.

ConversationStore optionally sets verified owner kind and ID as transaction-local
settings. The runtime factory always enables this. Policies on child tables check
the visible parent conversation. Missing owner settings fail closed. Owner scope
comes only from ProfileVerifier, not client account fields.

IMPORTANT: this is a trusted server identity model. A holder of the database
credential can set another owner's custom settings. RLS is defense-in-depth against
missing predicates/context, not cryptographic per-user authentication or protection
against a compromised service/SQL injection with arbitrary database access. Never
share this credential or use it for analytical SQL. Default PUBLIC catalog/temp/
extension privileges have not been globally revoked; only unrelated public/auth/
chatbot table access was checked. This is not a comprehensive database hardening audit.

Focused live checks passed with the hosted backend and supplied normal test user:
create/reload through HistoryService; synthetic user/assistant message save and
idempotency; contiguous sequences; cross-namespace and unset-owner isolation;
cross-owner insertion denial; two concurrent submissions with one winner; owned
deletion. Only newly created synthetic records were deleted. Login may create a
refresh session/update last_login. Synthetic replies were not LLM generated.

The runner D:\cc\chatbot\verify_history_live.py takes one JSON login object on stdin.
Never save real passwords in scripts, command history or reports. Focused live
checks do not replace deletion-race, lock-timeout, crash/lease/recovery and deployed
HTTP tests. No user-message submission or client-written assistant endpoint exists.

## Local opt-in only

```powershell
& 'D:\cc\chatbot\.venv\Scripts\python.exe' 'D:\cc\chatbot\serve_history.py' --backend-origin https://backend-crime-records-of-india.onrender.com --enable-history --port 8081
```

Add exact --frontend-origin only when needed for development CORS. The launcher is
still loopback-only and defaults to disabled storage without --enable-history.
No public server was launched/deployed; frontend remains disconnected. Readiness
still returns 503 until deployment probes exist. Generation remains disabled.

Provisioning script refuses existing objects/credentials, rehearses rollback,
then commits additive migrations. Do not rerun it: migrations are already applied.
If an operational failure occurs, inspect live state rather than deleting objects
or rotating credentials blindly. Never drop a populated history table to retry.

168 offline/loopback tests passed, plus the focused live smoke checks. Historical
documents saying migration 003 is unapplied are superseded by this milestone.
Analytical runtime credentials remain separate and unprovisioned.

Primary references reviewed: PostgreSQL row-security/CREATE POLICY documentation
and Supabase custom-role/shared-pooler connection documentation.