# Local authentication integration verification

## What is exercised

`D:\cc\chatbot\test_auth_integration.py` starts a short-lived Node fixture at an ephemeral port bound only to `127.0.0.1`. The fixture is `D:\cc\backend\backend\test-support\identityServer.cjs`.

The fixture mounts the real Express auth router, controllers, JWT middleware, account resolver, bcrypt password verification, and token helpers. Python uses the real `ProfileVerifier` over HTTP without transport mocks. Login/refresh/logout are also exercised over real loopback HTTP. Test-only signing keys and fixture accounts are created in the child process.

Before importing routes, the fixture replaces the database module in the Node module cache. This prevents loading the real DB module, its dotenv initialization, PostgreSQL pool, and credentials. Account and refresh-token queries instead use a strict in-memory fixture. It does not mount the entire application, CORS, production proxy/TLS, or rate limiting. Fixture control uses parent/child stdin/stdout, not an HTTP administration endpoint. No real accounts or deployed tokens are involved.

The child shuts down when parent stdin closes; the test cleanup waits for termination and kills a stuck child. An independent 60-second fixture lifetime bounds orphan processes. Fixture output is not application logging and must never be wired into production.

## Run explicitly

Requires Node on PATH, the existing backend node_modules, and the chatbot virtual environment. No new dependencies or Docker are needed.

```powershell
Set-Location 'D:\cc\chatbot'
& 'D:\cc\chatbot\.venv\Scripts\python.exe' -m unittest test_auth_integration -v
```

Ten tests passed: user/admin roundtrips with equal IDs, refresh roundtrip, deactivation, deletion without cross-table fallback, changed admin role, legacy/mismatched/expired/wrong-key tokens, database-error translation, maximum bigint, logout limitations, and inactive login rejection. Missing runtime dependencies fail this explicit suite rather than silently skipping it.

This suite opens local sockets and is separate from the 106-test offline-only command in `HISTORY_API.md`. Blanket unittest discovery includes it; use explicit module lists when network-free execution is required.

## What remains unverified

- Actual PostgreSQL account/status/role/ID values and the DB placeholder compatibility layer.
- Migration 003 syntax, constraints, transactions, rollback, row locking, request races, and cascaded deletion on a real PostgreSQL instance.
- Runtime role grants/policies and real cross-account database isolation.
- Deployed backend TLS, origin configuration, latency, authentication logs, real tokens, and frontend login/logout lifecycle.

No migration was applied and persistence stays disabled. For PostgreSQL tests, provide a dedicated disposable test database and explicit authorization to create/drop test objects. It must not be the existing application/indexing database. Do not fall back to `.env` credentials, infer a safe test database from its name, or run destructive tests merely because a DSN exists. A future integration harness should require explicit opt-in, verify database/role identity, fail if target objects already exist, and guarantee cleanup. Docker is optional, not required; a separately provisioned isolated PostgreSQL database is sufficient.

The logout test intentionally asserts that access tokens remain usable after refresh-token deletion. It is a regression record of a known limitation, NOT acceptance of completed session revocation. Real persistence/API deployment remains gated.