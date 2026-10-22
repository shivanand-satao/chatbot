import io
import json
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from render_app import create_app
from source_search import search, search_terms
from server_identity import IdentityRejected, IdentityUnavailable


class SearchTests(unittest.TestCase):
    def test_terms_are_bounded_plain_text(self):
        self.assertEqual(search_terms('What is the right to freedom?'), ['right', 'freedom'])
        self.assertEqual(search_terms('the is of'), [])
        self.assertLessEqual(len(search_terms(' '.join('term'+str(i) for i in range(50)))), 12)
        self.assertTrue(search_terms('गिरफ्तारी के अधिकार'))
        for value in (None, '', 'x' * 1501, {}):
            with self.assertRaises(ValueError):
                search_terms(value)

    def test_bound_parameters_and_honest_result(self):
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = [{'document_id': 1, 'id': 7, 'revision': 2}]
        result = search(db, 'freedom', 1)
        sql, params = db.execute.call_args.args
        self.assertNotIn('freedom', sql)
        self.assertEqual(params, (['freedom'], [], ['freedom'], ['freedom'], 1, 1))
        self.assertEqual(result['hits'][0]['source_id'], 'document:1:chunk:7:revision:2')
        self.assertFalse(result['answer_generated'])
        self.assertFalse(result['generation_allowed'])
        self.assertEqual(search(db, 'the is')['hits'], [])
        for value in (True, 0, '1', -1):
            with self.assertRaises(ValueError):
                search(db, 'freedom', value)

    def call(self, payload, *, secret='a' * 48, bearer='Bearer token', path='/api/chat/search', method='POST'):
        raw = json.dumps(payload).encode()
        env = {'PATH_INFO': path, 'REQUEST_METHOD': method, 'QUERY_STRING': '',
               'HTTP_X_CHATBOT_SERVICE_SECRET': secret, 'HTTP_AUTHORIZATION': bearer,
               'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(raw)),
               'wsgi.input': io.BytesIO(raw)}
        app = create_app({'BACKEND_ORIGIN': 'https://backend.example',
                          'CHATBOT_SERVICE_SECRET': 'a' * 48, 'RETRIEVAL_ENABLED': 'true',
                          'retrieval_db_url': 'test-dsn-not-used'})
        statuses = []
        body = b''.join(app(env, lambda status, headers: statuses.append(status)))
        return statuses[0], json.loads(body)

    def test_source_route_authenticated_and_bounded(self):
        with patch('source_http.search_connection') as connect, patch('server_identity.ProfileVerifier.verify') as verify:
            verify.return_value = object()
            connect.return_value.__enter__.return_value.execute.return_value.fetchall.return_value = []
            status, body = self.call({'question': 'freedom'})
            self.assertEqual(status, '200 OK')
            self.assertEqual(body['data']['status'], 'no_matches')
            self.assertEqual(self.call({'question': 'freedom'}, secret='wrong')[0], '401 Unauthorized')
            self.assertEqual(self.call({'question': 'freedom', 'owner_id': 2})[0], '400 Bad Request')
            verify.side_effect = IdentityRejected()
            self.assertEqual(self.call({'question': 'freedom'})[0], '401 Unauthorized')
            verify.side_effect = IdentityUnavailable()
            self.assertEqual(self.call({'question': 'freedom'})[0], '503 Service Unavailable')

    def test_oversized_question_before_database(self):
        with patch('source_http.search_connection') as connect, patch('server_identity.ProfileVerifier.verify'):
            self.assertEqual(self.call({'question': 'x' * 1501})[0], '400 Bad Request')
            connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()