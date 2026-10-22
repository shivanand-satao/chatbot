"""Authenticated source-search routes with bounded bodies and concurrency."""

import threading

from psycopg import Error as DatabaseError

from history_http import strict_json
from server_identity import IdentityRejected, IdentityUnavailable
from source_search import document_list, search, search_connection


class SourceHTTP:
    def __init__(self, verifier, dsn, responder):
        self.verifier, self.dsn, self.respond = verifier, dsn, responder
        self.slots = threading.BoundedSemaphore(2)

    def __call__(self, env, start):
        def error(status, code):
            return self.respond(start, status, {'success': False, 'error': {'code': code}})
        if not self.slots.acquire(blocking=False):
            return error('429 Too Many Requests', 'busy')
        try:
            self.verifier.verify(env.get('HTTP_AUTHORIZATION'))
            if env.get('QUERY_STRING') or env.get('HTTP_ORIGIN'):
                return error('400 Bad Request', 'invalid_request')
            path, method = env.get('PATH_INFO'), env.get('REQUEST_METHOD')
            if path == '/api/chat/documents' and method == 'GET':
                if env.get('CONTENT_LENGTH') not in (None, '', '0') or env.get('HTTP_TRANSFER_ENCODING'):
                    return error('400 Bad Request', 'unexpected_body')
                with search_connection(self.dsn) as db:
                    data = {'documents': document_list(db)}
            elif path == '/api/chat/search' and method == 'POST':
                length = env.get('CONTENT_LENGTH', '')
                if (not length.isascii() or not length.isdecimal() or len(length) > 5
                        or not 1 <= int(length) <= 12000 or env.get('HTTP_TRANSFER_ENCODING')
                        or env.get('HTTP_CONTENT_ENCODING')
                        or env.get('CONTENT_TYPE', '').lower() not in ('application/json', 'application/json; charset=utf-8')):
                    return error('400 Bad Request', 'invalid_body')
                raw = env['wsgi.input'].read(int(length))
                if len(raw) != int(length):
                    return error('400 Bad Request', 'incomplete_body')
                args = strict_json(raw.decode('utf-8'))
                if not isinstance(args, dict) or set(args) - {'question', 'document_id'}:
                    return error('400 Bad Request', 'invalid_request')
                from source_search import search_terms
                search_terms(args.get('question'))
                with search_connection(self.dsn) as db:
                    data = search(db, args['question'], args.get('document_id'))
            else:
                return error('405 Method Not Allowed', 'method_not_allowed')
            return self.respond(start, '200 OK', {'success': True, 'data': data})
        except IdentityRejected:
            return error('401 Unauthorized', 'authentication_required')
        except (ValueError, UnicodeError, TypeError):
            return error('400 Bad Request', 'invalid_request')
        except (DatabaseError, IdentityUnavailable, RuntimeError):
            return error('503 Service Unavailable', 'source_search_unavailable')
        finally:
            self.slots.release()