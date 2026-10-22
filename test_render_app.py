import io
import json
import unittest

from render_app import create_app


class RenderAppTests(unittest.TestCase):
    def setUp(self):
        self.settings = {'BACKEND_ORIGIN': 'https://backend.example',
                         'CHATBOT_SERVICE_SECRET': 'a' * 48}

    def call(self, path, secret=None):
        env = {'PATH_INFO': path, 'REQUEST_METHOD': 'GET', 'QUERY_STRING': '',
               'wsgi.input': io.BytesIO(), 'REMOTE_ADDR': '127.0.0.1'}
        if secret is not None:
            env['HTTP_X_CHATBOT_SERVICE_SECRET'] = secret
        result = []
        body = b''.join(create_app(self.settings)(env, lambda status, headers: result.append(status)))
        return result[0], json.loads(body)

    def test_liveness_not_readiness(self):
        status, body = self.call('/health/live')
        self.assertEqual(status, '200 OK')
        self.assertFalse(body['data']['history_enabled'])
        self.assertNotIn(self.settings['CHATBOT_SERVICE_SECRET'], json.dumps(body))

    def test_service_auth_required(self):
        for secret in (None, '', 'wrong', 'é', 'a' * 300):
            self.assertEqual(self.call('/api/conversations', secret)[0], '401 Unauthorized')

    def test_readiness_remains_unverified(self):
        self.assertEqual(self.call('/health/ready', 'a' * 48)[0], '503 Service Unavailable')

    def test_user_bearer_required_even_with_service_secret(self):
        self.assertEqual(self.call('/api/chat/conversations', 'a' * 48)[0], '401 Unauthorized')

    def test_fail_closed_configuration(self):
        for change in ({'CHATBOT_SERVICE_SECRET': 'REPLACE_' + 'x' * 48},
                       {'BACKEND_ORIGIN': 'http://external.example'},
                       {'GENERATION_ENABLED': 'true'}, {'RETRIEVAL_ENABLED': 'true'},
                       {'HISTORY_ENABLED': 'yes'}, {'HISTORY_ENABLED': 'true'}):
            with self.assertRaises(ValueError):
                create_app({**self.settings, **change})

    def test_no_secret_is_locked_bootstrap_only(self):
        self.settings['CHATBOT_SERVICE_SECRET'] = ''
        self.assertEqual(self.call('/health/live')[0], '200 OK')
        self.assertEqual(self.call('/api/chat/conversations', '')[0], '401 Unauthorized')
        with self.assertRaises(ValueError):
            create_app({**self.settings, 'HISTORY_ENABLED': 'true'})


if __name__ == '__main__':
    unittest.main()