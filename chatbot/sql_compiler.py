import re

from .catalog import get_columns, quote_identifier
from .schemas import QueryPlan


class QueryPlanError(ValueError):
    pass


def _column(entry: dict, name: str) -> dict:
    columns = get_columns(entry)
    if name not in columns:
        raise QueryPlanError(f"Unknown column: {name}")
    return columns[name]


def _is_numeric(details: dict) -> bool:
    data_type = details.get("type", "").casefold()
    return any(token in data_type for token in ("int", "numeric", "decimal", "double", "real", "float"))


def _metric_expression(entry: dict, column: str) -> str:
    details = _column(entry, column)
    identifier = quote_identifier(column)
    if _is_numeric(details):
        return identifier
    # Text-backed counters occur in the existing catalog. Casting is only
    # allowed after the exact column was validated against the catalog.
    return f"NULLIF(regexp_replace(CAST({identifier} AS text), '[^0-9.-]', '', 'g'), '')::numeric"


def _safe_alias(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", value):
        raise QueryPlanError("Invalid result alias")
    return value


def validate_plan(plan: QueryPlan, entry: dict) -> None:
    if plan.intent == "clarification":
        return
    if not plan.table or plan.table.casefold() != entry["table_name"].casefold():
        raise QueryPlanError("Query table is not in the selected catalog entry")
    for dimension in plan.dimensions:
        _column(entry, dimension)
    for filter_spec in plan.filters:
        _column(entry, filter_spec.column)
    for measure in plan.measures:
        if measure.aggregation != "count" and not measure.column:
            raise QueryPlanError("A non-count measure requires a column")
        if measure.column:
            _column(entry, measure.column)
        if measure.alias:
            _safe_alias(measure.alias)
    for sort_spec in plan.order_by:
        if sort_spec.column not in plan.dimensions and not any(
            (measure.alias or measure.column) == sort_spec.column for measure in plan.measures
        ):
            _column(entry, sort_spec.column)


def compile_plan(plan: QueryPlan, entry: dict, schema_name: str = "public") -> tuple[str, list]:
    validate_plan(plan, entry)
    if plan.intent == "clarification":
        raise QueryPlanError("Cannot compile a clarification plan")

    dimensions = [(quote_identifier(column), column) for column in plan.dimensions]
    select_parts = [f"{identifier} AS {quote_identifier(column)}" for identifier, column in dimensions]
    aliases: set[str] = set(plan.dimensions)

    for index, measure in enumerate(plan.measures):
        if measure.aggregation == "count":
            expression = "COUNT(*)"
            alias = measure.alias or "row_count"
        else:
            expression = f"{measure.aggregation.upper()}({_metric_expression(entry, measure.column)})"
            alias = measure.alias or f"{measure.aggregation}_{measure.column}"
        alias = _safe_alias(alias)
        if alias in aliases:
            raise QueryPlanError(f"Duplicate result alias: {alias}")
        aliases.add(alias)
        select_parts.append(f"{expression} AS {quote_identifier(alias)}")

    if not select_parts:
        raise QueryPlanError("Query plan must select a dimension or measure")

    table_sql = f"{quote_identifier(schema_name)}.{quote_identifier(entry['table_name'])}"
    sql_parts = [f"SELECT {', '.join(select_parts)} FROM {table_sql}"]
    params: list = []
    predicates: list[str] = []

    for filter_spec in plan.filters:
        identifier = quote_identifier(filter_spec.column)
        operator = filter_spec.operator
        if operator == "eq":
            predicates.append(f"{identifier} = %s")
            params.append(filter_spec.value)
        elif operator == "neq":
            predicates.append(f"{identifier} <> %s")
            params.append(filter_spec.value)
        elif operator == "in":
            if not isinstance(filter_spec.value, list) or not filter_spec.value:
                raise QueryPlanError("in filters require a non-empty list")
            predicates.append(f"{identifier} IN ({', '.join(['%s'] * len(filter_spec.value))})")
            params.extend(filter_spec.value)
        elif operator == "between":
            if not isinstance(filter_spec.value, list) or len(filter_spec.value) != 2:
                raise QueryPlanError("between filters require exactly two values")
            predicates.append(f"{identifier} BETWEEN %s AND %s")
            params.extend(filter_spec.value)
        elif operator in {"gte", "lte"}:
            predicates.append(f"{identifier} {'>=' if operator == 'gte' else '<='} %s")
            params.append(filter_spec.value)
        elif operator == "contains":
            predicates.append(f"CAST({identifier} AS text) ILIKE %s")
            params.append(f"%{filter_spec.value}%")

    if predicates:
        sql_parts.append("WHERE " + " AND ".join(predicates))
    if dimensions:
        sql_parts.append("GROUP BY " + ", ".join(identifier for identifier, _ in dimensions))
    if plan.order_by:
        order_parts = []
        for sort_spec in plan.order_by:
            if sort_spec.column in aliases:
                order_parts.append(f"{quote_identifier(sort_spec.column)} {sort_spec.direction.upper()}")
            else:
                _column(entry, sort_spec.column)
                order_parts.append(f"{quote_identifier(sort_spec.column)} {sort_spec.direction.upper()}")
        sql_parts.append("ORDER BY " + ", ".join(order_parts))

    sql_parts.append("LIMIT %s")
    params.append(plan.limit)
    return " ".join(sql_parts), params
