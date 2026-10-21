from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class FilterSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str = Field(min_length=1, max_length=128)
    operator: Literal["eq", "neq", "in", "between", "gte", "lte", "contains"]
    value: Any


class MeasureSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str | None = None
    aggregation: Literal["count", "sum", "avg", "min", "max"] = "count"
    alias: str | None = None


class SortSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str
    direction: Literal["asc", "desc"] = "asc"


class QueryPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Literal["aggregate", "rows", "clarification"] = "aggregate"
    table: str | None = None
    dimensions: list[str] = Field(default_factory=list)
    measures: list[MeasureSpec] = Field(default_factory=list)
    filters: list[FilterSpec] = Field(default_factory=list)
    order_by: list[SortSpec] = Field(default_factory=list)
    limit: int = Field(default=20, ge=1, le=100)
    clarification: str | None = None
