"""Render WSGI bootstrap. No local .env, model loading, or migration execution."""

import hmac
import json
import os
import re

from history_http import HistoryHTTP
from history_service import HistoryService
from server_identity import ProfileVerifier


def response(start_response, status, payload):
    body = json.dumps(payload).encode('utf-8')
    start_response(status, [('Content-Type', 'application/json'),
                            ('Content-Length', str(len(body))),
                            ('Cache-Control', 'no-store'),
                            ('X-Content-Type-Options', 'nosniff')])
    return [body]


def create_app(settings=None):
    settings = os.environ if settings is None else settings
    flags = {}
    for name in ('HISTORY_ENABLED', 'RETRIEVAL_ENABLED', 'GENERATION_ENABLED'):
        value = settings.get(name, 'false')
        if value not in ('true', 'false'):
            raise ValueError('Feature flags must be true or false')
        flags[name] = value == 'true'
    if flags['RETRIEVAL_ENABLED'] or flags['GENERATION_ENABLED']:
        raise ValueError('Retrieval and generation HTTP endpoints are not implemented')
    secret = settings.get('CHATBOT_SERVICE_SECRET', '')
    if secret and (not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', secret) or secret.startswith('REPLACE_')):
        raise ValueError('A strong backend service secret is required')
    if flags['HISTORY_ENABLED'] and not secret:
        raise ValueError('History requires backend service authentication')
    verifier = ProfileVerifier(settings.get('BACKEND_ORIGIN', ''))

    def unavailable_store(_owner):
        raise RuntimeError('History disabled')

    scope = unavailable_store
    if flags['HISTORY_ENABLED']:
        from history_runtime import store_factory
        dsn = settings.get('history_db_url', '')
        if not dsn or dsn.startswith('REPLACE_'):
            raise ValueError('Restricted history credential required')
        scope = store_factory(dsn)
    history = HistoryHTTP(HistoryService(verifier, scope, enabled=flags['HISTORY_ENABLED']))

    def application(environ, start_response):
        path = environ.get('PATH_INFO', '')
        if path == '/health/live':
            if environ.get('REQUEST_METHOD') != 'GET' or environ.get('QUERY_STRING'):
                return response(start_response, '400 Bad Request', {'success': False})
            return response(start_response, '200 OK', {'success': True, 'data': {
                'alive': True, 'history_enabled': flags['HISTORY_ENABLED'],
                'retrieval_enabled': False, 'generation_enabled': False}})
        provided = environ.get('HTTP_X_CHATBOT_SERVICE_SECRET', '')
        if (not secret or not isinstance(provided, str) or len(provided) > 256
                or not provided.isascii() or not hmac.compare_digest(provided, secret)):
            return response(start_response, '401 Unauthorized',
                            {'success': False, 'error': {'code': 'service_authentication_required'}})
        # User Authorization is independently verified by the existing backend profile API.
        # Never accept owner IDs/roles from headers, query parameters, or JSON.
        return history(environ, start_response)

    return application


# Gunicorn factory invocation avoids requiring secrets just to import for tests.