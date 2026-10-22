"""Dependency-free WSGI history adapter. No database/model configuration or writes.

Production gateway MUST reject duplicate Authorization/Content-Length headers,
bound header bytes and total request time, and enforce distributed rate limits.
WSGI can lose raw duplicate-header information before the application sees it.
"""

import json
import re
import threading
import time
from http import HTTPStatus
from urllib.parse import parse_qsl, urlsplit

from server_identity import IdentityRejected, bearer_header


class RequestError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def strict_json(value):
    def invalid_constant(_value):
        raise ValueError('Non-finite JSON value')
    return json.loads(value, object_pairs_hook=unique_object, parse_constant=invalid_constant)


def origin_value(value):
    if (not isinstance(value, str) or len(value) > 500
            or any(ord(c) <= 32 or ord(c) >= 127 for c in value)):
        raise ValueError('Invalid exact origin')
    parsed = urlsplit(value)
    if (parsed.scheme not in ('https', 'http') or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment or '\\' in value
            or '%' in parsed.netloc or parsed.port == 0):
        raise ValueError('Expected an exact HTTP(S) origin without a trailing slash')
    return value


class HistoryHTTP:
    def __init__(self, service, allowed_origins=(), max_inflight=4, requests_per_minute=60):
        if not isinstance(allowed_origins, (list, tuple)):
            raise ValueError('Expected an explicit origin list')
        self.origins = frozenset(origin_value(origin) for origin in allowed_origins)
        if type(max_inflight) is not int or not 1 <= max_inflight <= 32:
            raise ValueError('Invalid concurrency limit')
        if type(requests_per_minute) is not int or not 1 <= requests_per_minute <= 600:
            raise ValueError('Invalid rate limit')
        self.service = service
        self.slots = threading.BoundedSemaphore(max_inflight)
        self.rate_lock = threading.Lock()
        self.rates = {}
        self.rate = requests_per_minute

    def admitted(self, address):
        # Process-local, bounded state. Do not trust client Forwarded/XFF fields.
        now = time.monotonic()
        with self.rate_lock:
            expired = [key for key, (start, _) in self.rates.items() if now - start >= 60]
            for key in expired:
                del self.rates[key]
            if address not in self.rates:
                if len(self.rates) >= 1024:
                    return False
                self.rates[address] = (now, 0)
            start, count = self.rates[address]
            if count >= self.rate:
                return False
            self.rates[address] = (start, count + 1)
            return True

    def __call__(self, environ, start_response):
        origin = environ.get('HTTP_ORIGIN')
        headers = [('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff'),
                   ('Vary', 'Origin'), ('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'")]
        status, body = 500, self.error('internal_error')
        acquired = False
        try:
            if origin is not None:
                if origin not in self.origins:
                    raise RequestError(403, 'origin_not_allowed')
                headers.append(('Access-Control-Allow-Origin', origin))
            if not self.admitted(environ.get('REMOTE_ADDR', 'unknown')):
                raise RequestError(429, 'rate_limited')
            acquired = self.slots.acquire(blocking=False)
            if not acquired:
                raise RequestError(503, 'server_busy')
            status, body, extra = self.dispatch(environ)
            headers.extend(extra)
        except RequestError as error:
            status, body = error.status, self.error(error.code)
        except (ValueError, UnicodeError, RecursionError):
            status, body = 400, self.error('invalid_request')
        except Exception:
            # Never expose a traceback, SQL, credentials or model/transport errors.
            status, body = 500, self.error('internal_error')
        finally:
            if acquired:
                self.slots.release()
        if status in (429, 503):
            headers.append(('Retry-After', '60' if status == 429 else '5'))
        try:
            payload = b'' if body is None else json.dumps(body, ensure_ascii=True, allow_nan=False).encode('utf-8')
        except (ValueError, TypeError, RecursionError):
            status = 500
            payload = json.dumps(self.error('internal_error')).encode('utf-8')
        headers.extend([('Content-Type', 'application/json; charset=utf-8'), ('Content-Length', str(len(payload)))])
        start_response(f'{status} {HTTPStatus(status).phrase}', headers)
        return [payload]

    @staticmethod
    def error(code):
        return {'success': False, 'error': {'code': code}}

    @staticmethod
    def route(path):
        base = '/api/chat/conversations'
        if path == base:
            return {'GET': 'list', 'POST': 'create'}, {}
        if path == base + '/last-active':
            return {'GET': 'last_active'}, {}
        match = re.fullmatch(re.escape(base) + r'/([0-9a-f-]{36})(/messages)?', path)
        if match:
            methods = {'GET': 'messages'} if match[2] else {'GET': 'get', 'DELETE': 'delete'}
            return methods, {'conversation_id': match[1]}
        raise RequestError(404, 'not_found')

    def dispatch(self, environ):
        method = environ.get('REQUEST_METHOD', '')
        path = environ.get('PATH_INFO', '')
        query = environ.get('QUERY_STRING', '')
        if len(path) > 256 or len(query) > 2048:
            raise RequestError(414, 'request_target_too_long')
        if environ.get('history.duplicate_headers'):
            raise RequestError(400, 'ambiguous_headers')
        if method == 'GET' and path in ('/health/live', '/health/ready'):
            if query:
                raise RequestError(400, 'invalid_request')
            if path.endswith('/live'):
                return 200, {'success': True, 'data': {'alive': True}}, []
            # Never report DB/auth readiness without integration probes.
            return 503, self.error('readiness_unverified'), []
        methods, args = self.route(path)
        if method == 'OPTIONS':
            requested_method = environ.get('HTTP_ACCESS_CONTROL_REQUEST_METHOD')
            requested_headers = environ.get('HTTP_ACCESS_CONTROL_REQUEST_HEADERS', '')
            names = {name.strip().lower() for name in requested_headers.split(',') if name.strip()}
            if (not environ.get('HTTP_ORIGIN') or requested_method not in methods
                    or names - {'authorization', 'content-type'} or query):
                raise RequestError(400, 'invalid_preflight')
            return 204, None, [('Access-Control-Allow-Methods', ', '.join(sorted(methods))),
                               ('Access-Control-Allow-Headers', 'Authorization, Content-Type'),
                               ('Vary', 'Access-Control-Request-Method, Access-Control-Request-Headers')]
        if method not in methods:
            return 405, self.error('method_not_allowed'), [('Allow', ', '.join(sorted(methods)))]
        try:
            authorization = bearer_header(environ.get('HTTP_AUTHORIZATION'))
        except IdentityRejected:
            raise RequestError(401, 'authentication_required') from None
        if environ.get('HTTP_TRANSFER_ENCODING') or environ.get('HTTP_CONTENT_ENCODING'):
            raise RequestError(400, 'unsupported_body_encoding')
        raw_length = environ.get('CONTENT_LENGTH', '')
        if raw_length and (len(raw_length) > 8 or re.fullmatch('[0-9]+', raw_length) is None):
            raise RequestError(400, 'invalid_content_length')
        length = int(raw_length or '0')
        if length > 16384:
            raise RequestError(413, 'body_too_large')
        if method == 'POST':
            if query:
                raise RequestError(400, 'unexpected_query')
            if not raw_length:
                raise RequestError(411, 'content_length_required')
            if environ.get('CONTENT_TYPE', '').lower() not in ('application/json', 'application/json; charset=utf-8'):
                raise RequestError(415, 'json_required')
            raw = environ['wsgi.input'].read(length)
            if len(raw) != length:
                raise RequestError(400, 'incomplete_body')
            args = strict_json(raw.decode('utf-8'))
            if not isinstance(args, dict):
                raise RequestError(400, 'json_object_required')
        else:
            if length:
                raise RequestError(400, 'unexpected_body')
            pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True,
                              encoding='utf-8', errors='strict', max_num_fields=4)
            values = unique_object(pairs)
            allowed = {'limit', 'before'} if methods[method] == 'list' else (
                {'limit', 'before_sequence'} if methods[method] == 'messages' else set())
            if set(values) - allowed:
                raise RequestError(400, 'unexpected_query')
            for key, value in values.items():
                if key in ('limit', 'before_sequence'):
                    if re.fullmatch('[1-9][0-9]{0,18}', value) is None:
                        raise RequestError(400, 'invalid_cursor_or_limit')
                    args[key] = int(value)
                else:
                    args[key] = strict_json(value)
        status, body = self.service.handle(methods[method], authorization, args)
        return status, body, []