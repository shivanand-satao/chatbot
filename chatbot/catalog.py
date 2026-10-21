from collections.abc import Iterable

from django.conf import settings
from django.db import DatabaseError, connection

from .models import DatasetCatalogEntry

SYSTEM_TABLES = {
    "admins",
    "users",
    "refresh_tokens",
    "user_settings",
    "notifications",
    "dataset_metadata",
    "dataset_columns",
    "views_log",
    "search_logs",
    "admin_modification_logs",
    "api_request_logs",
    "upload_history",
    "response",
    "chatbot_schema_catalog",
    "chatbot_sessions",
    "chatbot_messages",
    "chatbot_documents",
}


def quote_identifier(identifier: str) -> str:
    if not isinstance(identifier, str) or not identifier or len(identifier) > 128:
        raise ValueError("Invalid SQL identifier")
    if "\x00" in identifier:
        raise ValueError("Invalid SQL identifier")
    return '"' + identifier.replace('"', '""') + '"'


def _normalise_entry(entry: DatasetCatalogEntry) -> dict:
    columns = entry.columns_schema if isinstance(entry.columns_schema, list) else []
    return {
        "table_name": entry.table_name,
        "crime_category": entry.crime_category or "",
        "row_count": entry.row_count or 0,
        "semantic_summary": entry.semantic_summary or "",
        "embedding_text": entry.embedding_text or entry.semantic_summary or "",
        "columns_schema": columns,
        "sample_values": entry.sample_values if isinstance(entry.sample_values, dict) else {},
    }


def _fallback_catalog() -> list[dict]:
    schema_name = quote_identifier(settings.DB_SCHEMA)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s
              AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """,
            [settings.DB_SCHEMA],
        )
        tables = [row[0] for row in cursor.fetchall()]

        records = []
        for table_name in tables:
            if table_name.lower() in SYSTEM_TABLES:
                continue
            cursor.execute(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY ordinal_position
                """,
                [settings.DB_SCHEMA, table_name],
            )
            columns = [
                {
                    "name": row[0],
                    "quoted_name": quote_identifier(row[0]),
                    "type": row[1],
                    "nullable": row[2],
                }
                for row in cursor.fetchall()
            ]
            records.append(
                {
                    "table_name": table_name,
                    "crime_category": "",
                    "row_count": 0,
                    "semantic_summary": f"Dataset table {table_name}.",
                    "embedding_text": f"Table: {table_name}",
                    "columns_schema": columns,
                    "sample_values": {},
                }
            )
    return records


def get_catalog() -> list[dict]:
    try:
        entries = list(DatasetCatalogEntry.objects.all())
    except DatabaseError:
        entries = []
    if entries:
        return [_normalise_entry(entry) for entry in entries if entry.table_name.lower() not in SYSTEM_TABLES]
    return _fallback_catalog()


def get_catalog_entry(table_name: str, catalog: Iterable[dict] | None = None) -> dict | None:
    records = catalog if catalog is not None else get_catalog()
    wanted = table_name.casefold()
    return next((record for record in records if record["table_name"].casefold() == wanted), None)


def get_columns(entry: dict) -> dict[str, dict]:
    result = {}
    for column in entry.get("columns_schema", []):
        name = column.get("name") or column.get("COLUMN_NAME")
        if name:
            result[str(name)] = {
                "name": str(name),
                "type": str(column.get("type") or column.get("DATA_TYPE") or "text"),
                "nullable": column.get("nullable") or column.get("IS_NULLABLE"),
            }
    return result
