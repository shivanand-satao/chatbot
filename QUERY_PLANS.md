# Structured query compiler — offline milestone

`query_plan.py` compiles a small validated structured plan into a psycopg SQL composition plus a separate parameter tuple. It never connects to the database or executes the statement. It is not a validator for arbitrary raw SQL; raw SQL, joins, CTEs, expressions, and unsupported fields are rejected by construction.

The trusted caller must supply a reviewed catalog mapping approved dataset names to exact column names and PostgreSQL data types. Never accept this catalog from the LLM/client, automatically approve every public table, or treat retrieved inferred descriptions as verified schema. Protected application tables are blocked additionally, but the denylist is not a replacement for the approved catalog.

Plan fields:

- Required: `table`, `columns` (explicit list; no wildcard selection).
- Optional: `filters`, `aggregates`, `group_by`, `order_by`, `limit`.
- Filters are AND-only, with exact columns and bounded numeric/text values. Numeric comparisons support eq/ne/gt/gte/lt/lte; text supports eq/ne. Null checks use is_null/not_null without a value.
- Aggregates: count, sum, avg, min, max. Only count permits `*`; other aggregates require a numeric column. Grouping must exactly match selected non-aggregate columns. Aliases must be distinct and cannot shadow source columns.
- Sorts reference selected outputs only, with asc/desc.
- Limits range from 1–200, default 100. List sizes, string lengths, and numeric values are bounded. This initial grammar does not support dates, booleans, OR, HAVING, IN, joins, DISTINCT, or offsets.

Example plan (illustrative column names; not verified against a specific live dataset):

```json
{
  "table": "auto_theft",
  "columns": ["Year"],
  "group_by": ["Year"],
  "aggregates": [{"function": "sum", "column": "Cases", "alias": "total"}],
  "filters": [{"column": "Area_name", "operator": "eq", "value": "Maharashtra"}],
  "order_by": [{"column": "Year", "direction": "asc"}],
  "limit": 20
}
```

The LLM planner will later produce this structured representation rather than unrestricted executable SQL. Filter values and limits remain bound parameters; identifiers use psycopg quoting and come from the approved catalog. No LLM planner is connected yet.

## Runtime gates still required

Before any execution: validate live schema and measure semantics, requested coverage, aggregate/component overlap, function resolution/search path, runtime privileges, query timeout, and output-size bounds. Restrict generated analytical execution to a dedicated reviewed read-only database role; do not use the elevated indexing credential. A row limit does not bound aggregate scan cost. Compiled SQL does not establish answerability, correct units, or statistical validity.

The compiler intentionally has no execute function. No raw-SQL parser dependency has been installed; a future raw-SQL interface would require its own structural validation and security review and is outside this milestone.

## Validation

49 offline tests passed across query plans, document context, retrieval, readiness, book parsing, and metadata indexing. Compiler tests cover parameter binding, identifier quoting, grouped aggregates, null predicates, protected tables, invented columns, unsupported query fields/functions, grouping mismatch, invalid types, and excessive limits. No Groq calls or database queries were made for this milestone.

Run from `D:\cc\chatbot`:

```powershell
& 'D:\cc\chatbot\.venv\Scripts\python.exe' -m unittest test_query_plan test_document_context test_retrieval test_readiness test_books test_metadata_indexing -v
```