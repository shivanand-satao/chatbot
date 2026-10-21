from django.conf import settings
from django.db import connection, transaction


def execute_query(sql: str, params: list) -> list[dict]:
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                [f"{settings.CHATBOT_QUERY_TIMEOUT_MS}ms"],
            )
            cursor.execute(sql, params)
            columns = [description[0] for description in cursor.description or []]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
