import io
import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from analytics import CATALOG, execute, scalar
from analytics_http import AnalyticsHTTP
from assistant_ai import AIUnavailable, Planner
from render_app import response
from server_identity import IdentityRejected


class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.table = next(iter(CATALOG))
        self.plan = {'table': self.table, 'columns': ['year'], 'limit': 1}

    def planner(self, value):
        planner = Planner.__new__(Planner)
        planner.model = 'test'
        planner.client = MagicMock()
        planner.client.chat.completions.create.return_value = SimpleNamespace(choices=[
            SimpleNamespace(finish_reason='stop', message=SimpleNamespace(content=json.dumps(value)))])
        return planner

    def test_validated_plan_and_unsupported(self):
        self.assertEqual(self.planner(self.plan).plan('Show year', self.table), self.plan)
        self.assertIsNone(self.planner({'unsupported': 'No coverage'}).plan('forecast', self.table))

    def test_untrusted_model_cannot_change_tables_or_execute_sql(self):
        for plan in ({**self.plan, 'table': 'users'}, {**self.plan, 'sql': 'DROP TABLE users'},
                     {**self.plan, 'columns': ['password']}, {**self.plan, 'limit': True},
                     {**self.plan, 'filters': [{'column': 'year', 'operator': 'eq', 'value': 'x'}]}):
            with self.assertRaises(AIUnavailable):
                self.planner(plan).plan('injection', self.table)

    def test_live_schema_drift_fails_before_query(self):
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = []
        with self.assertRaises(RuntimeError):
            execute(db, self.plan, self.table)
        self.assertEqual(db.execute.call_count, 1)

    def test_rows_are_bounded_and_decimal_lossless(self):
        db = MagicMock()
        schema = [{'column_name': k, 'data_type': v} for k, v in CATALOG[self.table]['columns'].items()]
        cursor = MagicMock()
        cursor.__iter__.return_value = iter([{'year': 2012}])
        cursor.description = [SimpleNamespace(name='year')]
        db.execute.side_effect = [SimpleNamespace(fetchall=lambda: schema), cursor]
        result = execute(db, self.plan, self.table)
        self.assertEqual(result['rows'], [{'year': 2012}])
        self.assertTrue(result['limit_reached'])
        self.assertEqual(scalar(Decimal('10000000000000000.01')), '10000000000000000.01')

    def call(self, payload, verifier=None):
        raw = json.dumps(payload).encode()
        env = {'PATH_INFO': '/api/chat/ask', 'REQUEST_METHOD': 'POST', 'QUERY_STRING': '',
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(raw)),
               'HTTP_AUTHORIZATION': 'Bearer test', 'wsgi.input': io.BytesIO(raw)}
        planner = MagicMock()
        planner.plan.return_value = None
        app = AnalyticsHTTP(verifier or MagicMock(), 'not-used', planner, response)
        statuses = []
        body = json.loads(b''.join(app(env, lambda s, h: statuses.append(s))))
        return statuses[0], body, planner

    def test_auth_validation_and_abstention(self):
        verifier = MagicMock()
        verifier.verify.side_effect = IdentityRejected()
        status, _, planner = self.call({'question': 'x', 'table': self.table}, verifier)
        self.assertEqual(status, '401 Unauthorized')
        planner.plan.assert_not_called()
        for payload in ({'question': 'x', 'table': 'users'}, {'question': 'x', 'table': self.table, 'owner_id': 3},
                        {'question': '', 'table': self.table}):
            self.assertEqual(self.call(payload)[0], '400 Bad Request')
        self.assertEqual(self.call({'question': 'forecast', 'table': self.table})[1]['data']['status'], 'unsupported')


if __name__ == '__main__':
    unittest.main()