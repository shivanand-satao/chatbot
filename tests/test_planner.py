from chatbot.planner import heuristic_plan


def test_heuristic_planner_builds_a_yearly_auto_theft_plan(monkeypatch):
    catalog = [
        {
            "table_name": "auto_theft",
            "crime_category": "Property Crime - Auto Theft",
            "semantic_summary": "Vehicle theft by state and year.",
            "columns_schema": [
                {"name": "Area_name", "type": "character varying"},
                {"name": "Year", "type": "integer"},
                {"name": "Auto_Theft_Stolen", "type": "integer"},
            ],
            "sample_values": {"Area_name": ["Delhi", "Bihar"]},
        }
    ]

    plan = heuristic_plan("Show the trend of stolen vehicles in Delhi from 2015 to 2020", catalog)

    assert plan.table == "auto_theft"
    assert plan.dimensions == ["Year"]
    assert plan.measures[0].column == "Auto_Theft_Stolen"
    assert plan.filters[0].value == [2015, 2020]
    assert plan.filters[1].value == "Delhi"
