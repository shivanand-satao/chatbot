"""HTTP boundary tests with fake storage/auth; no DB or Groq requests."""

import http.client
import io
import json
import threading
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock
from urllib.parse import urlencode
from uuid import uuid4
from wsgiref.simple_server import make_server

from conversation_context import Owner
from history_http import HistoryHTTP
from history_service import HistoryService
from serve_history import SafeDevelopmentHandler


class HistoryHTTPTests(unittest.TestCase):
    def setUp(self):
        self.service = MagicMock()
        self.service.handle.return_value = (200, {'success': True, 'data': {}})
        self.app = HistoryHTTP(self.service, allowed_origins=['https://frontend.example'])
        self.cid = str(uuid4())

    def call(self, method='GET', path='/api/chat/conversations', query='', body=b'', **overrides):
        env = {'REQUEST_METHOD': method, 'PATH_INFO': path, 'QUERY_STRING': query,
               'HTTP_AUTHORIZATION': 'Bearer test.token', 'REMOTE_ADDR': '127.0.0.1',
               'wsgi.input': io.BytesIO(body), 'CONTENT_LENGTH': str(len(body)),
               'CONTENT_TYPE': 'application/json'}
        env.update(overrides)
        result = {}

        def start_response(status, headers):
            result['status'] = int(status.split()[0])
            result['headers'] = headers

        data = b''.join(self.app(env, start_response))
        result['body'] = json.loads(data) if data else None
        self.assertEqual(int(dict(result['headers'])['Content-Length']), len(data))
        self.assertEqual(dict(result['headers'])['Cache-Control'], 'no-store')
        return result

    def test_route_mapping(self):
        for method, suffix, action in [('GET', '', 'list'), ('POST', '', 'create'),
            ('GET', '/last-active', 'last_active'), ('GET', '/' + self.cid, 'get'),
            ('DELETE', '/' + self.cid, 'delete'), ('GET', '/' + self.cid + '/messages', 'messages')]:
            result = self.call(method, '/api/chat/conversations' + suffix, body=b'{}' if method == 'POST' else b'')
            self.assertEqual(result['status'], 200)
            self.assertEqual(self.service.handle.call_args.args[0], action)

    def test_create_json_and_integer_query_values(self):
        self.call('POST', body=b'{"title":"Example"}')
        self.assertEqual(self.service.handle.call_args.args[2], {'title': 'Example'})
        self.call(query='limit=3')
        self.assertEqual(self.service.handle.call_args.args[2], {'limit': 3})
        self.call(path='/api/chat/conversations/' + self.cid + '/messages', query='limit=5&before_sequence=8')
        self.assertEqual(self.service.handle.call_args.args[2], {'conversation_id': self.cid, 'limit': 5, 'before_sequence': 8})

    def test_cursor_query_json(self):
        cursor = {'created_at': '2026-10-22T00:00:00+00:00', 'id': self.cid}
        self.call(query=urlencode({'before': json.dumps(cursor)}))
        self.assertEqual(self.service.handle.call_args.args[2], {'before': cursor})

    def test_missing_or_ambiguous_authorization(self):
        for value in (None, 'Bearer a,Bearer b', 'Bearer a\r\nX: y'):
            self.assertEqual(self.call(HTTP_AUTHORIZATION=value)['status'], 401)
        self.assertEqual(self.call(**{'history.duplicate_headers': True})['status'], 400)
        self.service.handle.assert_not_called()

    def test_unknown_and_generation_routes_are_not_exposed(self):
        for suffix in ('/generate', '/finish-turn', '/' + self.cid + '/submit'):
            self.assertEqual(self.call(path='/api/chat/conversations' + suffix)['status'], 404)
        self.service.handle.assert_not_called()

    def test_method_not_allowed(self):
        result = self.call('PUT')
        self.assertEqual(result['status'], 405)
        self.assertEqual(dict(result['headers'])['Allow'], 'GET, POST')

    def test_exact_cors_and_no_cookie_credentials(self):
        result = self.call(HTTP_ORIGIN='https://frontend.example')
        self.assertEqual(dict(result['headers'])['Access-Control-Allow-Origin'], 'https://frontend.example')
        self.assertNotIn('Access-Control-Allow-Credentials', dict(result['headers']))
        for origin in ('null', 'https://frontend.example.evil', 'https://frontend.example/'):
            result = self.call(HTTP_ORIGIN=origin)
            self.assertEqual(result['status'], 403)
            self.assertNotIn('Access-Control-Allow-Origin', dict(result['headers']))

    def test_preflight_does_not_authenticate(self):
        result = self.call('OPTIONS', HTTP_ORIGIN='https://frontend.example',
            HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST', HTTP_ACCESS_CONTROL_REQUEST_HEADERS='authorization,content-type')
        self.assertEqual(result['status'], 204)
        self.assertIsNone(result['body'])
        self.service.handle.assert_not_called()

    def test_preflight_rejects_unsupported_method_headers_and_missing_origin(self):
        for values in ({'HTTP_ACCESS_CONTROL_REQUEST_METHOD': 'PUT'},
                       {'HTTP_ACCESS_CONTROL_REQUEST_METHOD': 'POST', 'HTTP_ACCESS_CONTROL_REQUEST_HEADERS': 'X-Owner'},
                       {'HTTP_ACCESS_CONTROL_REQUEST_METHOD': 'POST', 'HTTP_ORIGIN': None}):
            env = {'HTTP_ORIGIN': 'https://frontend.example'}
            env.update(values)
            self.assertEqual(self.call('OPTIONS', **env)['status'], 400)

    def test_invalid_json_duplicate_keys_and_constants(self):
        for body in (b'[]', b'null', b'{', b'{"title":"a","title":"b"}',
                     b'{"limit":NaN}', b'\xff'):
            self.assertEqual(self.call('POST', body=body)['status'], 400)
        self.service.handle.assert_not_called()

    def test_body_limit_and_length_checks(self):
        self.assertEqual(self.call('POST', CONTENT_LENGTH='16385')['status'], 413)
        self.assertEqual(self.call('POST', CONTENT_LENGTH='')['status'], 411)
        self.assertEqual(self.call('POST', CONTENT_LENGTH='10', body=b'{}')['status'], 400)
        for value in ('-1', '1,2', '0x10', '999999999'):
            self.assertEqual(self.call('POST', CONTENT_LENGTH=value)['status'], 400)

    def test_wrong_content_type_encoding_and_unexpected_bodies(self):
        self.assertEqual(self.call('POST', body=b'{}', CONTENT_TYPE='text/plain')['status'], 415)
        self.assertEqual(self.call(HTTP_TRANSFER_ENCODING='chunked')['status'], 400)
        self.assertEqual(self.call(HTTP_CONTENT_ENCODING='gzip')['status'], 400)
        self.assertEqual(self.call(body=b'{}')['status'], 400)
        self.assertEqual(self.call('POST', query='limit=1', body=b'{}')['status'], 400)

    def test_query_limits_duplicates_and_unknown_fields(self):
        for query in ('limit=1&limit=2', 'owner_id=7', 'limit=-1', 'limit=01',
                      'limit=true', 'before=not-json', 'limit', 'a=1&b=2&c=3&d=4&e=5'):
            self.assertEqual(self.call(query=query)['status'], 400)
        self.assertEqual(self.call(query='a' * 2049)['status'], 414)
        self.service.handle.assert_not_called()

    def test_health_never_claims_database_ready(self):
        self.assertEqual(self.call(path='/health/live', HTTP_AUTHORIZATION=None)['status'], 200)
        self.assertEqual(self.call(path='/health/ready', HTTP_AUTHORIZATION=None)['status'], 503)
        self.service.handle.assert_not_called()

    def test_unexpected_service_failure_is_sanitized(self):
        self.service.handle.side_effect = RuntimeError('secret password and SQL')
        result = self.call()
        self.assertEqual(result['status'], 500)
        self.assertNotIn('secret', json.dumps(result['body']))
        self.assertTrue(self.app.slots.acquire(blocking=False))
        self.app.slots.release()

    def test_rate_limit_and_retry_after(self):
        self.app = HistoryHTTP(self.service, requests_per_minute=1)
        self.assertEqual(self.call()['status'], 200)
        result = self.call()
        self.assertEqual(result['status'], 429)
        self.assertEqual(dict(result['headers'])['Retry-After'], '60')

    def test_invalid_response_serialization_is_sanitized(self):
        self.service.handle.return_value = (200, {'private': object()})
        result = self.call()
        self.assertEqual(result['status'], 500)
        self.assertEqual(result['body']['error']['code'], 'internal_error')

    def test_concurrency_limit(self):
        self.app = HistoryHTTP(self.service, max_inflight=1)
        self.app.slots.acquire()
        try:
            self.assertEqual(self.call()['status'], 503)
            self.service.handle.assert_not_called()
        finally:
            self.app.slots.release()

    def test_origin_configuration_rejects_wildcards_and_paths(self):
        for value in ('*', 'https://x/path', 'https://user:password@x', 'https://x/', 'https://x\r\n'):
            with self.assertRaises(ValueError):
                HistoryHTTP(self.service, allowed_origins=[value])

    def test_real_history_service_with_fake_store(self):
        verifier = MagicMock()
        verifier.verify.return_value = Owner('user', 7)
        scope = MagicMock()
        store = scope.return_value.__enter__.return_value
        store.owner = Owner('user', 7)
        store.create.return_value = {'id': uuid4(), 'title': 'Example', 'revision': 1,
                                    'created_at': datetime.now(timezone.utc), 'updated_at': datetime.now(timezone.utc)}
        self.app = HistoryHTTP(HistoryService(verifier, scope, enabled=True))
        result = self.call('POST', body=b'{"title":"Example"}')
        self.assertEqual(result['status'], 201)
        self.assertEqual(result['body']['data']['conversation']['title'], 'Example')
        self.assertEqual(self.call('POST', body=b'{"owner_id":8}')['status'], 400)

    def test_disabled_service_never_opens_storage(self):
        verifier = MagicMock()
        verifier.verify.return_value = Owner('user', 7)
        scope = MagicMock()
        self.app = HistoryHTTP(HistoryService(verifier, scope))
        self.assertEqual(self.call()['status'], 503)
        scope.assert_not_called()


class LocalHTTPTests(unittest.TestCase):
    def test_real_loopback_http_and_duplicate_header_rejection(self):
        service = MagicMock()
        service.handle.return_value = (200, {'success': True, 'data': {'conversations': []}})
        server = make_server('127.0.0.1', 0, HistoryHTTP(service), handler_class=SafeDevelopmentHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for duplicate, expected in ((False, 200), (True, 400)):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
                try:
                    connection.putrequest('GET', '/api/chat/conversations')
                    connection.putheader('Authorization', 'Bearer test')
                    if duplicate:
                        connection.putheader('Authorization', 'Bearer second')
                    connection.endheaders()
                    response = connection.getresponse()
                    self.assertEqual(response.status, expected)
                    json.loads(response.read())
                finally:
                    connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()