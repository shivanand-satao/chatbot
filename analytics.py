"""Restricted dataset execution with live-schema checks and bounded results."""

import json
from contextlib import contextmanager
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row

from assistant_ai import CATALOG
from query_plan import compile_plan

ROLE = 'chatbot_analytics'
NOTICE = ('Experimental AI-selected query, not a verified statistical conclusion. '
          'Review the query plan, exact filters and raw records. Units, total/component overlap '
          'and dataset coverage may be unverified. No current crime rates, forecasts or causal '
          'claims. Questions/results are not saved. Questions and the selected public schema '
          'are sent to Groq; returned database rows are not sent to Groq.')


@contextmanager
def analytics_connection(dsn):
    with psycopg.connect(dsn, sslmode='require', connect_timeout=5, row_factory=dict_row) as db:
        db.execute('SET TRANSACTION READ ONLY')
        db.execute("SET LOCAL statement_timeout='5000ms'")
        db.execute("SET LOCAL lock_timeout='1000ms'")
        role = db.execute('''SELECT current_user AS name,rolsuper,rolbypassrls,rolcreaterole,
            rolcreatedb,rolreplication FROM pg_roles WHERE rolname=current_user''').fetchone()
        if role['name'] != ROLE or any(role[k] for k in role if k != 'name'):
            raise RuntimeError('Dedicated analytics identity required')
        if db.execute('SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=current_user)').fetchone():
            raise RuntimeError('Analytics memberships are not permitted')
        yield db
        db.rollback()


def scalar(value):
    if value is None or isinstance(value, (str, int, bool)):
        return value[:4000] if isinstance(value, str) else value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        import math
        return value if math.isfinite(value) else str(value)
    return str(value)[:4000]


def execute(db, plan, table):
    expected = CATALOG[table]['columns']
    actual = {r['column_name']: r['data_type'] for r in db.execute('''
        SELECT column_name,data_type FROM information_schema.columns
        WHERE table_schema='public' AND table_name=%s''', (table,)).fetchall()}
    if actual != expected:
        raise RuntimeError('Dataset schema changed; refresh approved catalog')
    compiled = compile_plan(plan, {table: actual})
    cursor = db.execute(compiled.statement, compiled.parameters)
    rows, size = [], 0
    truncated = False
    for row in cursor:
        item = {k: scalar(v) for k, v in row.items()}
        size += len(json.dumps(item).encode('utf-8'))
        if size > 150000 or len(rows) >= 100:
            truncated = True
            break
        rows.append(item)
    return {'mode': 'data', 'status': 'query_result', 'table': table, 'plan': plan,
            'columns': [item.name for item in cursor.description], 'rows': rows,
            'returned_rows': len(rows), 'display_truncated': truncated,
            'limit_reached': len(rows) >= plan.get('limit', 100),
            'notice': NOTICE, 'answer_generated': False}