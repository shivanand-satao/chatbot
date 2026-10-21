import unittest

from query_plan import compile_plan

CATALOG = {'auto_theft': {'Year': 'integer', 'Area_name': 'text', 'Cases': 'bigint'},
           'odd"table': {'odd"column': 'integer'}}


class QueryPlanTests(unittest.TestCase):
    def test_values_are_bound_and_not_interpolated(self):
        value = "x'; DROP TABLE users; --"
        result = compile_plan({'table': 'auto_theft', 'columns': ['Area_name'],
                               'filters': [{'column': 'Area_name', 'operator': 'eq', 'value': value}]}, CATALOG)
        self.assertNotIn(value, result.statement.as_string())
        self.assertEqual(result.parameters, (value, 100))

    def test_grouped_sum_and_sort(self):
        result = compile_plan({'table': 'auto_theft', 'columns': ['Year'], 'group_by': ['Year'],
            'aggregates': [{'function': 'sum', 'column': 'Cases', 'alias': 'total'}],
            'order_by': [{'column': 'total', 'direction': 'desc'}], 'limit': 5}, CATALOG)
        self.assertEqual(result.statement.as_string(),
            'SELECT "Year", SUM("Cases") AS "total" FROM "public"."auto_theft" GROUP BY "Year" ORDER BY "total" DESC LIMIT %s')
        self.assertEqual(result.parameters, (5,))

    def test_count_and_null_filter(self):
        result = compile_plan({'table': 'auto_theft', 'columns': [],
            'aggregates': [{'function': 'count', 'column': '*', 'alias': 'rows'}],
            'filters': [{'column': 'Year', 'operator': 'not_null'}]}, CATALOG)
        self.assertIn('COUNT(*)', result.statement.as_string())
        self.assertIn('"Year" IS NOT NULL', result.statement.as_string())

    def test_identifier_quoting(self):
        result = compile_plan({'table': 'odd"table', 'columns': ['odd"column']}, CATALOG)
        self.assertIn('"odd""table"', result.statement.as_string())

    def test_protected_tables_even_if_catalog_contains_them(self):
        with self.assertRaises(ValueError):
            compile_plan({'table': 'users', 'columns': ['id']}, {'users': {'id': 'integer'}})

    def test_rejects_invalid_plans(self):
        base = {'table': 'auto_theft', 'columns': ['Year']}
        invalid = [dict(base, sql='DELETE FROM users'), dict(base, table='unknown'),
            dict(base, columns=['missing']), dict(base, columns=['Year', 'Year']),
            dict(base, columns=[]), dict(base, limit=True), dict(base, limit=201),
            dict(base, group_by=['Year']), dict(base, joins=[]),
            dict(base, order_by=[{'column': 'Cases', 'direction': 'asc'}]),
            dict(base, aggregates=[{'function': 'pg_sleep', 'column': 'Year', 'alias': 'bad'}]),
            dict(base, aggregates=[{'function': 'sum', 'column': 'Area_name', 'alias': 'bad'}]),
            dict(base, aggregates=[{'function': 'count', 'column': '*', 'alias': 'Year'}]),
            dict(base, aggregates=[{'function': 'count', 'column': '*', 'alias': 'n'}])]
        for plan in invalid:
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                compile_plan(plan, CATALOG)

    def test_rejects_invalid_filters(self):
        for item in [
            {'column': 'Year', 'operator': 'eq', 'value': True},
            {'column': 'Year', 'operator': 'eq', 'value': 1.5},
            {'column': 'Year', 'operator': 'eq', 'value': float('nan')},
            {'column': 'Year', 'operator': 'eq', 'value': '2020'},
            {'column': 'Year', 'operator': 'eq', 'value': None},
            {'column': 'Year', 'operator': 'is_null', 'value': None},
            {'column': 'Year', 'operator': 'OR 1=1', 'value': 1},
            {'column': 'Area_name', 'operator': 'gt', 'value': 'x'},
        ]:
            with self.subTest(item=item), self.assertRaises(ValueError):
                compile_plan({'table': 'auto_theft', 'columns': ['Year'], 'filters': [item]}, CATALOG)


if __name__ == '__main__':
    unittest.main()