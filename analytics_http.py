"""Authenticated analytics catalog and query endpoints."""

import threading

from psycopg import Error as DatabaseError

from analytics import CATALOG, NOTICE, analytics_connection, execute
from assistant_ai import AIUnavailable
from history_http import strict_json
from server_identity import IdentityRejected, IdentityUnavailable


class AnalyticsHTTP:
    def __init__(self, verifier, dsn, planner, responder):
        self.verifier, self.dsn, self.planner, self.respond = verifier, dsn, planner, responder
        self.slots = threading.BoundedSemaphore(1)

    def __call__(self, env, start):
        def reply(status, payload):
            return self.respond(start, status, payload)
        def error(status, code):
            return reply(status, {'success': False, 'error': {'code': code}})
        if not self.slots.acquire(blocking=False):
            return error('429 Too Many Requests', 'busy')
        try:
            self.verifier.verify(env.get('HTTP_AUTHORIZATION'))
            if env.get('QUERY_STRING') or env.get('HTTP_ORIGIN') or env.get('HTTP_TRANSFER_ENCODING'):
                raise ValueError('Invalid request')
            path, method = env.get('PATH_INFO'), env.get('REQUEST_METHOD')
            if path == '/api/chat/datasets' and method == 'GET':
                if env.get('CONTENT_LENGTH') not in (None, '', '0'):
                    raise ValueError('Unexpected body')
                data = {'datasets': [{'table': k, 'label': k.replace('_', ' ')} for k in CATALOG]}
            elif path == '/api/chat/ask' and method == 'POST':
                length = env.get('CONTENT_LENGTH', '')
                if (not isinstance(length, str) or not length.isascii() or not length.isdecimal()
                        or len(length) > 5 or not 1 <= int(length) <= 12000
                        or env.get('HTTP_CONTENT_ENCODING')
                        or env.get('CONTENT_TYPE', '').lower() not in ('application/json', 'application/json; charset=utf-8')):
                    raise ValueError('Invalid body')
                raw = env['wsgi.input'].read(int(length))
                if len(raw) != int(length):
                    raise ValueError('Incomplete body')
                args = strict_json(raw.decode('utf-8'))
                if not isinstance(args, dict) or set(args) != {'question', 'table'}:
                    raise ValueError('Invalid fields')
                question, table = args['question'], args['table']
                if (not isinstance(question, str) or not question.strip() or len(question) > 1500
                        or '\x00' in question or not isinstance(table, str) or table not in CATALOG):
                    raise ValueError('Invalid question or dataset')
                plan = self.planner.plan(question, table)
                if plan is None:
                    data = {'mode': 'data', 'status': 'unsupported', 'rows': [], 'notice': NOTICE,
                            'message': 'The question cannot be safely mapped to this dataset. '
                                       'Try a precise raw-record question with a year and location.'}
                else:
                    with analytics_connection(self.dsn) as db:
                        data = execute(db, plan, table)
            else:
                return error('405 Method Not Allowed', 'method_not_allowed')
            return reply('200 OK', {'success': True, 'data': data})
        except IdentityRejected:
            return error('401 Unauthorized', 'authentication_required')
        except (ValueError, UnicodeError, TypeError):
            return error('400 Bad Request', 'invalid_request')
        except (AIUnavailable, DatabaseError, IdentityUnavailable, RuntimeError):
            return error('503 Service Unavailable', 'analytics_unavailable')
        finally:
            self.slots.release()