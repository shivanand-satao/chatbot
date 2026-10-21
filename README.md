# Crime Records Chatbot

Self-contained Django service for conversational access to the existing Crime Records PostgreSQL database.

This project intentionally does not modify the existing `/backend` or `/frontend` projects. It verifies the existing backend JWT format, reads the existing `chatbot_schema_catalog` when available, and owns only its chatbot tables:

- `chatbot_sessions`
- `chatbot_messages`
- `chatbot_documents`

## API

Public health endpoints:

```text
GET /health/live
GET /health/ready
```

Authenticated endpoints using the existing bearer access token:

```text
GET  /v1/catalog
GET  /v1/sessions
POST /v1/sessions
GET  /v1/sessions/{id}
DELETE /v1/sessions/{id}
GET  /v1/sessions/{id}/messages
POST /v1/sessions/{id}/messages
```

The message endpoint accepts:

```json
{
  "message": "Which state had the most auto thefts between 2015 and 2020?"
}
```

The service produces a structured query plan, validates table and column names against the catalog, compiles parameterized PostgreSQL, executes read-only SQL with a statement timeout, and persists the user/assistant messages and citations.

## Local setup

Python 3.11+ is required.

```text
python -m venv .venv
.venv\\Scripts\\activate
python -m pip install -e ".[dev]"
copy .env.example .env
python manage.py migrate
python manage.py runserver 8001
```

Use the same `JWT_ACCESS_SECRET` and database connection settings as the existing backend. Do not copy real secrets into `.env.example` or source control.

## Catalog indexing

After `chatbot_documents` has been migrated and an embedding provider is configured:

```text
python manage.py reindex_catalog
```

The command reads the existing `chatbot_schema_catalog` table. If that table is absent, it falls back to `information_schema` and creates basic dataset documents.

## Design boundaries

- The LLM may produce a JSON query plan, never executable SQL.
- Dataset and column identifiers are allowlisted from catalog metadata.
- Queries are parameterized and run in read-only transactions.
- Sessions are isolated by both JWT role type and principal ID.
- The frontend and existing Express backend are not modified by this project.
