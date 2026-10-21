"""Compile a deliberately small structured query grammar; never execute SQL.

The approved catalog must be supplied by trusted server code, not by the model.
This is not a validator for arbitrary LLM-generated SQL.
"""

import math
from dataclasses import dataclass

from psycopg import sql

NUMERIC = {'smallint', 'integer', 'bigint', 'numeric', 'decimal', 'real', 'double precision'}
TEXT = {'text', 'character varying', 'character'}
PROTECTED = {'users', 'admins', 'refresh_tokens', 'user_settings', 'notifications',
             'dataset_metadata', 'dataset_columns', 'views_log', 'search_logs',
             'admin_modification_logs', 'response', 'api_request_logs', 'upload_history',
             'chatbot_schema_catalog', 'chatbot_catalog'}
OPERATORS = {'eq': '=', 'ne': '<>', 'gt': '>', 'gte': '>=', 'lt': '<', 'lte': '<='}
AGGREGATES = {'count': 'COUNT', 'sum': 'SUM', 'avg': 'AVG', 'min': 'MIN', 'max': 'MAX'}


@dataclass(frozen=True)
class CompiledPlan:
    statement: object
    parameters: tuple
    warnings: tuple


def keys(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise ValueError('Missing or unsupported query-plan fields')


def bounded_list(value, maximum):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError('Expected a bounded list')
    return value


def compile_plan(plan, catalog):
    keys(plan, {'table', 'columns'}, {'filters', 'aggregates', 'group_by', 'order_by', 'limit'})
    table = plan['table']
    if not isinstance(table, str) or table.casefold() in PROTECTED or table not in catalog:
        raise ValueError('Table is not approved for analytical queries')
    schema = catalog[table]

    def column(name):
        if not isinstance(name, str) or name not in schema:
            raise ValueError('Unknown column')
        return sql.Identifier(name)

    columns = bounded_list(plan['columns'], 20)
    for name in columns:
        column(name)
    if len(set(columns)) != len(columns):
        raise ValueError('Duplicate selected columns')
    groups = bounded_list(plan.get('group_by', []), 10)
    for name in groups:
        column(name)
    if len(set(groups)) != len(groups):
        raise ValueError('Duplicate grouping columns')
    aggregates = bounded_list(plan.get('aggregates', []), 10)
    projections = [column(name) for name in columns]
    outputs = set(columns)
    for aggregate in aggregates:
        keys(aggregate, {'function', 'column', 'alias'})
        function, name, alias = aggregate['function'], aggregate['column'], aggregate['alias']
        if not isinstance(function, str) or function not in AGGREGATES:
            raise ValueError('Unsupported aggregate function')
        if (not isinstance(alias, str) or not alias.isascii() or not alias.isidentifier()
                or len(alias) > 63 or alias in outputs or alias in schema):
            raise ValueError('Invalid or ambiguous aggregate alias')
        expression = sql.SQL('*') if name == '*' and function == 'count' else column(name)
        if name != '*' and function != 'count' and schema[name] not in NUMERIC:
            raise ValueError('Aggregate requires a numeric column')
        projections.append(sql.SQL('{}({}) AS {}').format(
            sql.SQL(AGGREGATES[function]), expression, sql.Identifier(alias)))
        outputs.add(alias)
    if not projections:
        raise ValueError('At least one selected column or aggregate is required')
    if groups and not aggregates:
        raise ValueError('Grouping without aggregates is not supported')
    if aggregates and set(columns) != set(groups):
        raise ValueError('Selected non-aggregate columns must exactly match group_by')
    limit = plan.get('limit', 100)
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError('Limit must be an integer from 1 to 200')
    parameters, predicates = [], []
    for item in bounded_list(plan.get('filters', []), 20):
        keys(item, {'column', 'operator'}, {'value'})
        name, operator = item['column'], item['operator']
        identifier = column(name)
        if operator in ('is_null', 'not_null'):
            if 'value' in item:
                raise ValueError('Null predicates must not include a value')
            predicates.append(sql.SQL('{} IS {}NULL').format(
                identifier, sql.SQL('NOT ' if operator == 'not_null' else '')))
            continue
        if not isinstance(operator, str) or operator not in OPERATORS or 'value' not in item:
            raise ValueError('Unsupported filter operation')
        value, kind = item['value'], schema[name]
        if kind in NUMERIC:
            if type(value) not in (int, float) or abs(value) > 1e18 or not math.isfinite(value):
                raise ValueError('Numeric filter value is invalid')
            if kind in {'smallint', 'integer', 'bigint'} and type(value) is not int:
                raise ValueError('Integer column requires an integer filter')
        elif kind in TEXT:
            if not isinstance(value, str) or len(value) > 1000 or '\x00' in value:
                raise ValueError('Text filter value is invalid')
            if operator not in ('eq', 'ne'):
                raise ValueError('Text range filters are not supported')
        else:
            raise ValueError('Filter type is not supported by this grammar')
        predicates.append(sql.SQL('{} {} %s').format(identifier, sql.SQL(OPERATORS[operator])))
        parameters.append(value)
    statement = sql.SQL('SELECT {} FROM {}').format(
        sql.SQL(', ').join(projections), sql.Identifier('public', table))
    if predicates:
        statement += sql.SQL(' WHERE ') + sql.SQL(' AND ').join(predicates)
    if groups:
        statement += sql.SQL(' GROUP BY ') + sql.SQL(', ').join(column(name) for name in groups)
    orders = []
    for order in bounded_list(plan.get('order_by', []), 10):
        keys(order, {'column', 'direction'})
        name, direction = order['column'], order['direction']
        if not isinstance(name, str) or name not in outputs or direction not in ('asc', 'desc'):
            raise ValueError('Sort must reference a selected output and asc/desc')
        orders.append(sql.SQL('{} {}').format(sql.Identifier(name), sql.SQL(direction.upper())))
    if orders:
        statement += sql.SQL(' ORDER BY ') + sql.SQL(', ').join(orders)
    statement += sql.SQL(' LIMIT %s')
    parameters.append(limit)
    return CompiledPlan(statement, tuple(parameters), (
        'Compiled only; no SQL execution or answerability approval.',
        'Verify live schema, units, coverage, overlapping totals and restricted runtime privileges.',
        'Result limit does not bound aggregate scan cost; execution requires timeouts.',
        'Without a unique explicit sort, result ordering is not guaranteed.',
    ))