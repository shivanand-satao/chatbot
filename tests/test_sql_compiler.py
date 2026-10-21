from chatbot.schemas import FilterSpec, MeasureSpec, QueryPlan, SortSpec
from chatbot.sql_compiler import compile_plan


def entry():
    return {
        "table_name": "auto_theft",
        "columns_schema": [
            {"name": "Area_name", "type": "character varying"},
            {"name": "Year", "type": "integer"},
            {"name": "Auto_Theft_Coordinated/Traced", "type": "text"},
            {"name": "Auto_Theft_Stolen", "type": "integer"},
        ],
    }


def test_compiler_quotes_unusual_columns_and_parameterizes_values():
    plan = QueryPlan(
        table="auto_theft",
        dimensions=["Year"],
        measures=[MeasureSpec(column="Auto_Theft_Stolen", aggregation="sum", alias="stolen")],
        filters=[FilterSpec(column="Area_name", operator="eq", value="Delhi")],
        order_by=[SortSpec(column="Year", direction="asc")],
        limit=20,
    )

    sql, params = compile_plan(plan, entry())

    assert '"Auto_Theft_Stolen"' in sql
    assert '"Area_name" = %s' in sql
    assert '"auto_theft"' in sql
    assert params == ["Delhi", 20]


def test_compiler_supports_text_backed_numeric_metrics():
    plan = QueryPlan(
        table="auto_theft",
        measures=[
            MeasureSpec(
                column="Auto_Theft_Coordinated/Traced",
                aggregation="sum",
                alias="traced",
            )
        ],
    )

    sql, _ = compile_plan(plan, entry())

    assert "regexp_replace" in sql
    assert "::numeric" in sql
