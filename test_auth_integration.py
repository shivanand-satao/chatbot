"""Loopback HTTP integration, real backend auth routes/JWTs, fake DB only.

Explicit suite (not part of the offline-only command). Requires installed backend
node_modules and node on PATH. Does not read .env, use real keys, or access a DB.
"""

import copy
import http.client
import json
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import unittest
from urllib.parse import urlsplit

from conversation_context import Owner
from server_identity import IdentityRejected, IdentityUnavailable, ProfileVerifier


class AuthIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        node = shutil.which('node')
        if node is None:
            raise RuntimeError('Node is required for this explicit integration suite')
        fixture = Path(__file__).resolve().parent.parent / 'backend/backend/test-support/identityServer.cjs'
        cls.process = subprocess.Popen([node, str(fixture)], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, encoding='utf-8', cwd=str(fixture.parent))
        cls.addClassCleanup(cls.close_fixture)
        cls.output = queue.Queue()

        def read_stdout():
            for line in cls.process.stdout:
                cls.output.put(line)
            cls.output.put(None)

        cls.reader = threading.Thread(target=read_stdout, daemon=True)
        cls.reader.start()
        cls.origin = cls.receive()['origin']
        cls.address = urlsplit(cls.origin)
        cls.verifier = ProfileVerifier(cls.origin, allow_loopback_http=True, timeout=3)

    @classmethod
    def close_fixture(cls):
        if cls.process.poll() is None:
            cls.process.stdin.close()
            try:
                cls.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.process.kill()
                cls.process.wait(timeout=5)
        for stream in (cls.process.stdin, cls.process.stdout, cls.process.stderr):
            if not stream.closed:
                stream.close()

    @classmethod
    def receive(cls):
        try:
            line = cls.output.get(timeout=10)
        except queue.Empty:
            raise RuntimeError('Backend integration fixture timed out') from None
        if line is None:
            raise RuntimeError('Backend integration fixture exited unexpectedly')
        payload = json.loads(line)
        if 'error' in payload:
            raise RuntimeError('Backend fixture rejected a command')
        return payload

    def command(self, **values):
        self.process.stdin.write(json.dumps(values) + '\n')
        self.process.stdin.flush()
        return self.receive()

    def setUp(self):
        self.accounts = {
            'user': {'id': '7', 'username': 'fixture-user', 'status': 'active'},
            'admin': {'id': '7', 'username': 'fixture-admin', 'status': 'active', 'role': 'admin'}}
        self.command(action='reset', accounts=self.accounts)

    def request(self, endpoint, body=None, authorization=None):
        connection = http.client.HTTPConnection(self.address.hostname, self.address.port, timeout=3)
        headers = {'Connection': 'close'}
        if authorization:
            headers['Authorization'] = authorization
        if body is not None:
            headers['Content-Type'] = 'application/json'
        try:
            connection.request('POST' if body is not None else 'GET', '/api/auth/' + endpoint,
                               body=json.dumps(body) if body is not None else None, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read(16384)), dict(response.getheaders())
        finally:
            connection.close()

    def login(self, kind):
        status, body, _ = self.request('login-' + kind, {
            'username': self.accounts[kind]['username'], 'password': 'fixture-password'})
        self.assertEqual(status, 200)
        return body

    def test_user_and_same_id_admin_roundtrip(self):
        for kind in ('user', 'admin'):
            tokens = self.login(kind)
            header = 'Bearer ' + tokens['accessToken']
            self.assertEqual(self.verifier.verify(header), Owner(kind, 7))
            status, body, headers = self.request('profile', authorization=header)
            self.assertEqual(status, 200)
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertEqual(set(body['user']), {'id', 'username', 'role', 'account_kind', 'status'})

    def test_refresh_then_verifier_roundtrip(self):
        tokens = self.login('admin')
        status, body, _ = self.request('refresh-token', {'refreshToken': tokens['refreshToken']})
        self.assertEqual(status, 200)
        self.assertEqual(self.verifier.verify('Bearer ' + body['accessToken']), Owner('admin', 7))

    def test_account_deactivation_is_rechecked_for_profile_and_refresh(self):
        tokens = self.login('user')
        self.accounts['user']['status'] = 'banned'
        self.command(action='state', accounts=self.accounts)
        with self.assertRaises(IdentityRejected):
            self.verifier.verify('Bearer ' + tokens['accessToken'])
        self.assertEqual(self.request('refresh-token', {'refreshToken': tokens['refreshToken']})[0], 403)

    def test_deletion_never_falls_back_to_same_id_admin(self):
        tokens = self.login('user')
        del self.accounts['user']
        self.command(action='state', accounts=self.accounts)
        with self.assertRaises(IdentityRejected):
            self.verifier.verify('Bearer ' + tokens['accessToken'])

    def test_admin_role_change_requires_new_login(self):
        tokens = self.login('admin')
        self.accounts['admin']['role'] = 'moderator'
        self.command(action='state', accounts=self.accounts)
        with self.assertRaises(IdentityRejected):
            self.verifier.verify('Bearer ' + tokens['accessToken'])
        new_tokens = self.login('admin')
        self.assertEqual(self.verifier.verify('Bearer ' + new_tokens['accessToken']), Owner('admin', 7))

    def test_legacy_mismatched_expired_and_wrong_key_tokens(self):
        valid = {'id': '7', 'role': 'user', 'account_kind': 'user'}
        for claims, extra in (({'id': '7', 'role': 'user'}, {}),
                              ({'id': '7', 'role': 'user', 'account_kind': 'admin'}, {}),
                              (valid, {'expired': True}), (valid, {'wrong_key': True})):
            token = self.command(action='sign', claims=claims, **extra)['token']
            with self.assertRaises(IdentityRejected):
                self.verifier.verify('Bearer ' + token)

    def test_database_failure_is_reported_as_identity_unavailable(self):
        tokens = self.login('user')
        self.command(action='state', accounts=self.accounts, database_failure=True)
        with self.assertRaises(IdentityUnavailable):
            self.verifier.verify('Bearer ' + tokens['accessToken'])

    def test_maximum_bigint_roundtrip_preserves_identity(self):
        self.accounts['user']['id'] = '9223372036854775807'
        self.command(action='state', accounts=self.accounts)
        tokens = self.login('user')
        self.assertEqual(self.verifier.verify('Bearer ' + tokens['accessToken']),
                         Owner('user', 9223372036854775807))

    def test_logout_revokes_refresh_but_not_access_documented_limitation(self):
        tokens = self.login('user')
        self.assertEqual(self.request('logout', {'refreshToken': tokens['refreshToken']})[0], 200)
        self.assertEqual(self.request('refresh-token', {'refreshToken': tokens['refreshToken']})[0], 403)
        self.assertEqual(self.verifier.verify('Bearer ' + tokens['accessToken']), Owner('user', 7))

    def test_inactive_login_does_not_issue_tokens(self):
        snapshot = copy.deepcopy(self.accounts)
        for kind in ('user', 'admin'):
            self.accounts = copy.deepcopy(snapshot)
            self.accounts[kind]['status'] = 'inactive'
            self.command(action='state', accounts=self.accounts)
            status, body, _ = self.request('login-' + kind, {
                'username': self.accounts[kind]['username'], 'password': 'fixture-password'})
            self.assertEqual(status, 403)
            self.assertNotIn('accessToken', body)


if __name__ == '__main__':
    unittest.main()