import json
import re
from typing import Any

import httpx
from django.conf import settings

from .catalog import get_columns
from .retrieval import retrieve_catalog
from .schemas import FilterSpec, MeasureSpec, QueryPlan, SortSpec


def _column_name(column: dict) -> str:
    return str(column.get("name") or column.get("COLUMN_NAME"))


def _numeric_columns(entry: dict) -> list[str]:
    dimensions = {"area_name", "year", "group_name", "sub_group_name", "subgroup", "sub_group"}
    result = []
    for name, details in get_columns(entry).items():
        if name.casefold() in dimensions:
            continue
        data_type = details["type"].casefold()
        if any(token in data_type for token in ("int", "numeric", "decimal", "double", "real", "float")):
            result.append(name)
    return result


def _find_metric(entry: dict, question: str) -> str | None:
    columns = list(get_columns(entry))
    question_lower = question.casefold()
    preferred = [
        column for column in columns
        if column.casefold().replace("_", " ") in question_lower
    ]
    if preferred:
        return preferred[0]

    keywords = {
        "stolen": "stolen",
        "recovered": "recovered",
        "reported": "reported",
        "convicted": "convicted",
        "deaths": "deaths",
        "victims": "victim",
        "cases": "case",
        "loss": "loss",
        "total": "total",
    }
    for keyword, fragment in keywords.items():
        if keyword in question_lower:
            matches = [column for column in columns if fragment in column.casefold()]
            if matches:
                return matches[0]

    numeric = _numeric_columns(entry)
    return numeric[0] if numeric else None


def _find_area(entry: dict, question: str) -> str | None:
    samples = entry.get("sample_values", {})
    question_lower = question.casefold()
    for column, values in samples.items():
        if column.casefold() not in {"area_name", "state", "state_name"}:
            continue
        for value in values or []:
            if str(value).casefold() in question_lower:
                return str(value)
    return None


def heuristic_plan(question: str, candidates: list[dict] | None = None) -> QueryPlan:
    candidates = candidates or retrieve_catalog(question)
    if not candidates:
        return QueryPlan(
            intent="clarification",
            clarification="I could not identify a crime-records dataset for that question.",
        )

    question_lower = question.casefold()
    entry = candidates[0]
    table = entry["table_name"]
    columns = get_columns(entry)
    dimensions: list[str] = []
    filters: list[FilterSpec] = []
    order_by: list[SortSpec] = []

    years = [int(value) for value in re.findall(r"\b(19\d{2}|20\d{2})\b", question)]
    if years:
        year_column = next((name for name in columns if name.casefold() == "year"), None)
        if year_column:
            filters.append(
                FilterSpec(
                    column=year_column,
                    operator="between" if len(years) > 1 else "eq",
                    value=[min(years), max(years)] if len(years) > 1 else years[0],
                )
            )

    area = _find_area(entry, question)
    area_column = next((name for name in columns if name.casefold() == "area_name"), None)
    if area and area_column:
        filters.append(FilterSpec(column=area_column, operator="eq", value=area))

    asks_for_state = any(token in question_lower for token in ("which state", "by state", "by area", "across states"))
    asks_for_trend = any(token in question_lower for token in ("trend", "over time", "by year", "each year"))
    if asks_for_state and area_column:
        dimensions.append(area_column)
    elif asks_for_trend:
        year_column = next((name for name in columns if name.casefold() == "year"), None)
        if year_column:
            dimensions.append(year_column)

    metric = _find_metric(entry, question)
    wants_count = "how many rows" in question_lower or "number of records" in question_lower
    if wants_count or metric is None:
        measures = [MeasureSpec(aggregation="count", alias="row_count")]
    else:
        measures = [MeasureSpec(column=metric, aggregation="sum", alias=f"sum_{metric}")]

    if asks_for_state or "highest" in question_lower or "most" in question_lower or "largest" in question_lower:
        order_column = measures[0].alias or measures[0].column or "row_count"
        order_by.append(SortSpec(column=order_column, direction="desc"))

    return QueryPlan(
        intent="aggregate",
        table=table,
        dimensions=dimensions,
        measures=measures,
        filters=filters,
        order_by=order_by,
        limit=20,
    )


def _llm_prompt(question: str, candidates: list[dict]) -> str:
    compact = []
    for entry in candidates:
        compact.append(
            {
                "table_name": entry["table_name"],
                "crime_category": entry.get("crime_category"),
                "semantic_summary": entry.get("semantic_summary"),
                "columns_schema": entry.get("columns_schema", []),
                "sample_values": entry.get("sample_values", {}),
            }
        )
    return (
        "Create a safe JSON query plan for an Indian crime-records PostgreSQL database. "
        "Return only JSON with keys intent, table, dimensions, measures, filters, order_by, limit, clarification. "
        "Never return SQL. Use only the supplied tables and exact column names. "
        "Allowed aggregations are count, sum, avg, min, max. "
        f"Question: {question}\nCatalog candidates: {json.dumps(compact, default=str)}"
    )


def llm_plan(question: str, candidates: list[dict]) -> QueryPlan | None:
    if not settings.GROQ_API_KEY:
        return None
    try:
        response = httpx.post(
            f"{settings.GROQ_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.GROQ_API_KEY}"},
            json={
                "model": settings.GROQ_MODEL,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": "You generate validated query-plan JSON, never SQL."},
                    {"role": "user", "content": _llm_prompt(question, candidates)},
                ],
            },
            timeout=20,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return QueryPlan.model_validate(json.loads(content))
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return None


def plan_question(question: str) -> tuple[QueryPlan, list[dict]]:
    candidates = retrieve_catalog(question)
    return llm_plan(question, candidates) or heuristic_plan(question, candidates), candidates
