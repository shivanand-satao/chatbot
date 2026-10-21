from decimal import Decimal
from datetime import date, datetime


def _display(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def answer_from_rows(rows: list[dict], table: str) -> str:
    if not rows:
        return f"No matching records were found in `{table}`."
    if len(rows) == 1:
        values = "; ".join(f"{key}: {_display(value)}" for key, value in rows[0].items())
        return f"Result from `{table}`: {values}."

    headers = list(rows[0])
    lines = [" | ".join(headers), " | ".join("---" for _ in headers)]
    for row in rows[:20]:
        lines.append(" | ".join(str(_display(row.get(header, ""))) for header in headers))
    suffix = "" if len(rows) <= 20 else f"\n\nShowing the first 20 of {len(rows)} result rows."
    return f"Results from `{table}`:\n\n" + "\n".join(lines) + suffix
