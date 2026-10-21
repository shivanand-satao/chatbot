import re

from django.conf import settings
from django.db import connection

from .catalog import get_catalog
from .documents import embed_text


def _tokens(value: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", value.casefold()) if token}


def retrieve_catalog(question: str, limit: int = 5) -> list[dict]:
    vector_candidates = _retrieve_by_vector(question, limit)
    if vector_candidates:
        return vector_candidates

    question_tokens = _tokens(question)
    scored = []
    for record in get_catalog():
        searchable = " ".join(
            [
                record.get("table_name", ""),
                record.get("crime_category", ""),
                record.get("semantic_summary", ""),
                record.get("embedding_text", ""),
                " ".join(record.get("sample_values", {}).keys()),
            ]
        )
        score = len(question_tokens & _tokens(searchable))
        if record.get("table_name", "").casefold() in question.casefold():
            score += 10
        scored.append((score, record))
    scored.sort(key=lambda item: (item[0], item[1].get("row_count", 0)), reverse=True)
    return [record for score, record in scored[:limit] if score > 0] or [record for _, record in scored[:limit]]


def _retrieve_by_vector(question: str, limit: int) -> list[dict]:
    if settings.EMBEDDING_PROVIDER != "openai" or not settings.OPENAI_API_KEY:
        return []
    try:
        vector = embed_text(question)
        vector_literal = "[" + ",".join(str(value) for value in vector) + "]"
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT source_key
                FROM chatbot_documents
                WHERE namespace = %s AND source_type = %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                ["crime-records", "dataset_schema", vector_literal, limit],
            )
            source_keys = [row[0] for row in cursor.fetchall()]
        records = {record["table_name"]: record for record in get_catalog()}
        return [records[key] for key in source_keys if key in records]
    except Exception:
        # Retrieval remains useful before embeddings are configured or while
        # the document projection is being rebuilt.
        return []
