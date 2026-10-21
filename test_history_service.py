"""Offline boundary tests: no backend calls and no database connections."""

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

from psycopg import OperationalError

from conversation_context import Owner
from history_service import HistoryService
from server_identity import (IdentityRejected, IdentityUnavailable, ProfileVerifier,
                             bearer_header, owner_from_profile)


class IdentityTests(unittest.TestCase):
    def profile(self, account_id=7, role='user'):
        return {'success': True, 'user': {'id': account_id, 'role': role,
                'account_kind': 'user' if role == 'user' else 'admin', 'status': 'active'}}

    def test_role_namespaces(self):
        for role in ('user', 'admin', 'superadmin', 'moderator'):
            self.assertEqual(owner_from_profile(self.profile(role=role)),
                             Owner('user' if role == 'user' else 'admin', 7))

    def test_decimal_bigint_and_invalid_ids(self):
        self.assertEqual(owner_from_profile(self.profile('9223372036854775807')).account_id,
                         9223372036854775807)
        for account_id in (True, 0, -1, 1.0, '01', ' 7', '+7', 9007199254740992,
                           '9223372036854775808', None, []):
            with self.assertRaises(IdentityUnavailable):
                owner_from_profile(self.profile(account_id))

    def test_explicit_active_namespace_is_required(self):
        for field, value in (('account_kind', None), ('account_kind', 'admin'),
                             ('status', None), ('status', 'banned')):
            payload = self.profile()
            payload['user'][field] = value
            with self.assertRaises(IdentityRejected):
                owner_from_profile(payload)

    def test_invalid_profiles_and_roles(self):
        for payload in (None, [], {}, {'success': 1}, {'success': True, 'user': []}):
            with self.assertRaises(IdentityUnavailable):
                owner_from_profile(payload)
        for role in ('owner', 'USER', None, []):
            with self.assertRaises(IdentityRejected):
                owner_from_profile(self.profile(role=role))

    def test_header_rejects_injection_and_ambiguous_input(self):
        self.assertEqual(bearer_header('Bearer a.b.c'), 'Bearer a.b.c')
        for header in (None, '', 'bearer abc', 'Bearer ', 'Bearer a b', 'Bearer abc\r\nX: y',
                       'Bearer ' + 'x' * 8192, ['Bearer abc']):
            with self.assertRaises(IdentityRejected):
                bearer_header(header)

    def test_origin_configuration(self):
        ProfileVerifier('https://backend.example:443')
        ProfileVerifier('http://127.0.0.1:3000', allow_loopback_http=True)
        for origin in ('http://backend.example', 'https://x/path', 'https://a:b@x',
                       'https://x?token=x', 'https://x/#x', 'https://x:0',
                       'https://x:bad', 'https://x\\evil', 'https://x\n', '//x',
                       'http://127.0.0.1:3000'):
            with self.assertRaises(ValueError):
                ProfileVerifier(origin)

    def fake_response(self, connection, status=200, body=None, content_type='application/json'):
        response = connection.return_value.getresponse.return_value
        response.status = status
        response.getheader.side_effect = lambda name, default=None: {
            'Content-Type': content_type, 'Content-Encoding': 'identity'}.get(name, default)
        response.read.return_value = json.dumps(self.profile()).encode() if body is None else body
        return response

    @patch('server_identity.http.client.HTTPSConnection')
    def test_fixed_path_bounded_read_and_close(self, connection):
        response = self.fake_response(connection)
        owner = ProfileVerifier('https://backend.example').verify('Bearer a.b.c')
        self.assertEqual(owner, Owner('user', 7))
        self.assertEqual(connection.return_value.request.call_args.args, ('GET', '/api/auth/profile'))
        response.read.assert_called_once_with(16385)
        connection.return_value.close.assert_called_once()

    @patch('server_identity.http.client.HTTPSConnection')
    def test_redirects_and_server_errors_do_not_retry(self, connection):
        for status in (301, 302, 429, 500):
            self.fake_response(connection, status=status)
            with self.assertRaises(IdentityUnavailable):
                ProfileVerifier('https://backend.example').verify('Bearer abc')
        self.assertEqual(connection.return_value.request.call_count, 4)

    @patch('server_identity.http.client.HTTPSConnection')
    def test_denied_and_bad_responses(self, connection):
        for status in (401, 403):
            self.fake_response(connection, status=status)
            with self.assertRaises(IdentityRejected):
                ProfileVerifier('https://backend.example').verify('Bearer abc')
        for body in (b'not-json', b'\xff', b'x' * 16385):
            self.fake_response(connection, body=body)
            with self.assertRaises(IdentityUnavailable):
                ProfileVerifier('https://backend.example').verify('Bearer abc')
        self.fake_response(connection, content_type='text/html')
        with self.assertRaises(IdentityUnavailable):
            ProfileVerifier('https://backend.example').verify('Bearer abc')

    @patch('server_identity.http.client.HTTPSConnection')
    def test_network_failure_is_sanitized_and_closed(self, connection):
        connection.return_value.request.side_effect = TimeoutError('secret diagnostic')
        with self.assertRaises(IdentityUnavailable) as caught:
            ProfileVerifier('https://backend.example').verify('Bearer abc')
        self.assertNotIn('secret', str(caught.exception))
        connection.return_value.close.assert_called_once()


class HistoryServiceTests(unittest.TestCase):
    def setUp(self):
        self.owner = Owner('user', 7)
        self.verifier = MagicMock()
        self.verifier.verify.return_value = self.owner
        self.scope = MagicMock()
        self.store = self.scope.return_value.__enter__.return_value
        self.store.owner = self.owner
        self.service = HistoryService(self.verifier, self.scope, enabled=True)
        self.cid = uuid4()
        now = datetime.now(timezone.utc)
        self.row = {'id': self.cid, 'title': 'Example', 'revision': 1,
                    'created_at': now, 'updated_at': now, 'owner_id': 7, 'owner_kind': 'user'}

    def call(self, action, args=None):
        return self.service.handle(action, 'Bearer test', args)

    def test_disabled_by_default_without_opening_storage(self):
        service = HistoryService(self.verifier, self.scope)
        self.assertEqual(service.handle('list', 'Bearer test')[0], 503)
        self.scope.assert_not_called()

    def test_auth_failures_never_open_storage(self):
        for error, status in ((IdentityRejected(), 401), (IdentityUnavailable(), 503)):
            self.verifier.verify.side_effect = error
            self.assertEqual(self.call('list')[0], status)
        self.scope.assert_not_called()

    def test_create_serializes_and_does_not_expose_owner(self):
        self.store.create.return_value = self.row
        status, body = self.call('create', {'title': 'Example'})
        self.assertEqual(status, 201)
        self.scope.assert_called_once_with(self.owner)
        self.assertNotIn('owner_id', body['data']['conversation'])
        json.dumps(body)

    def test_owner_role_and_answer_fields_rejected(self):
        for args in ({'owner_id': 8}, {'role': 'admin'}, {'content': 'fake answer'}):
            self.assertEqual(self.call('create', args)[0], 400)
        self.scope.assert_not_called()

    def test_invalid_pagination_and_ids(self):
        for action, args in (('list', {'limit': True}), ('list', {'before': {}}),
                             ('messages', {'conversation_id': 'bad'}),
                             ('messages', {'conversation_id': str(self.cid), 'before_sequence': 0}),
                             ('list', {'before': {'id': str(self.cid), 'created_at': '2026-10-22'}})):
            self.assertEqual(self.call(action, args)[0], 400)
        self.scope.assert_not_called()

    def test_list_cursor_roundtrip(self):
        self.store.list_conversations.return_value = [self.row]
        status, body = self.call('list', {'limit': 1})
        self.assertEqual(status, 200)
        cursor = body['data']['next_before']
        self.call('list', {'limit': 1, 'before': cursor})
        self.assertEqual(self.store.list_conversations.call_args.kwargs['before'],
                         (self.row['created_at'], self.cid))

    def test_get_and_last_active(self):
        self.store.get.return_value = self.row
        self.assertEqual(self.call('get', {'conversation_id': str(self.cid)})[0], 200)
        self.store.get.assert_called_once_with(conversation_id=self.cid)
        self.store.last_active.return_value = None
        self.assertIsNone(self.call('last_active')[1]['data']['conversation'])

    def test_message_cursor_and_serialization(self):
        self.store.messages.return_value = [{'sequence': 5, 'request_id': uuid4(), 'role': 'user',
            'content': 'question', 'status': 'complete', 'sources': [], 'created_at': self.row['created_at']}]
        status, body = self.call('messages', {'conversation_id': str(self.cid), 'limit': 1})
        self.assertEqual(status, 200)
        self.assertEqual(body['data']['next_before_sequence'], 5)
        json.dumps(body)

    def test_delete_and_foreign_owner_errors(self):
        self.assertEqual(self.call('delete', {'conversation_id': str(self.cid)})[1]['data'], {'deleted': True})
        self.store.delete.side_effect = PermissionError('sensitive account information')
        status, body = self.call('delete', {'conversation_id': str(self.cid)})
        self.assertEqual(status, 404)
        self.assertNotIn('sensitive', json.dumps(body))

    def test_database_error_is_sanitized(self):
        self.store.create.side_effect = OperationalError('private DSN')
        status, body = self.call('create')
        self.assertEqual(status, 503)
        self.assertNotIn('private', json.dumps(body))

    def test_generation_and_client_answer_actions_unavailable(self):
        for action in ('begin_turn', 'finish_turn', 'generate'):
            self.assertEqual(self.call(action)[0], 404)
        self.scope.assert_not_called()

    def test_storage_identity_mismatch_fails_closed(self):
        self.store.owner = Owner('admin', 7)
        self.assertEqual(self.call('create')[0], 503)
        self.store.create.assert_not_called()


if __name__ == '__main__':
    unittest.main()