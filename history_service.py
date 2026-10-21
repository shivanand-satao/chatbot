"""Offline-testable history API boundary, NOT an HTTP server or router.

The future adapter supplies parsed JSON, a single Authorization header and an
action selected by its route table. store_scope(owner) must yield a fresh
ConversationStore using a connection owned by that scope. Default: disabled.
"""

from datetime import datetime
from uuid import UUID

from psycopg import Error as DatabaseError

from conversation_context import Owner, positive, text
from conversation_store import ConversationConflict, page_limit
from server_identity import IdentityRejected, IdentityUnavailable


def parse_uuid(value):
    if not isinstance(value, str):
        raise ValueError('Expected a UUID string')
    parsed = UUID(value)
    if str(parsed) != value:
        raise ValueError('Expected a canonical UUID string')
    return parsed


def conversation_view(row):
    return {key: wire_value(row[key]) for key in
            ('id', 'title', 'revision', 'created_at', 'updated_at')}


def wire_value(value):
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def message_view(row):
    return {key: wire_value(row[key]) for key in
            ('sequence', 'request_id', 'role', 'content', 'status', 'sources', 'created_at')}


class HistoryService:
    ACTIONS = {'create', 'list', 'last_active', 'get', 'messages', 'delete'}

    def __init__(self, verifier, store_scope, enabled=False):
        if type(enabled) is not bool:
            raise ValueError('enabled must be boolean')
        self.verifier, self.store_scope, self.enabled = verifier, store_scope, enabled

    def handle(self, action, authorization, arguments=None):
        """Returns (HTTP status, JSON-safe envelope); never returns tokens or DB errors."""
        try:
            owner = self.verifier.verify(authorization)
            if not isinstance(owner, Owner):
                raise IdentityUnavailable('Invalid verifier result')
            if not self.enabled:
                return self.error(503, 'history_unavailable')
            if not isinstance(action, str) or action not in self.ACTIONS:
                return self.error(404, 'not_found')
            arguments = {} if arguments is None else arguments
            parsed = self.validate(action, arguments)
            with self.store_scope(owner) as store:
                if store.owner != owner:
                    raise IdentityUnavailable('Storage identity mismatch')
                result = self.dispatch(store, action, parsed)
            return (201 if action == 'create' else 200), {'success': True, 'data': result}
        except IdentityRejected:
            return self.error(401, 'authentication_required')
        except IdentityUnavailable:
            return self.error(503, 'identity_unavailable')
        except PermissionError:
            return self.error(404, 'conversation_unavailable')
        except ConversationConflict:
            return self.error(409, 'conversation_conflict')
        except (ValueError, TypeError, OverflowError):
            return self.error(400, 'invalid_request')
        except DatabaseError:
            return self.error(503, 'history_unavailable')

    @staticmethod
    def error(status, code):
        return status, {'success': False, 'error': {'code': code}}

    @staticmethod
    def validate(action, args):
        fields = {'create': {'title'}, 'list': {'limit', 'before'}, 'last_active': set(),
                  'get': {'conversation_id'}, 'delete': {'conversation_id'},
                  'messages': {'conversation_id', 'limit', 'before_sequence'}}
        if not isinstance(args, dict) or set(args) - fields[action]:
            raise ValueError('Unsupported request fields')
        result = dict(args)
        if action in ('get', 'delete', 'messages'):
            result['conversation_id'] = parse_uuid(args.get('conversation_id'))
        if action == 'create':
            result['title'] = args.get('title', 'New conversation')
            text(result['title'], 200)
        if action in ('list', 'messages'):
            result['limit'] = args.get('limit', 20 if action == 'list' else 50)
            page_limit(result['limit'])
        if 'before_sequence' in args:
            positive(args['before_sequence'])
        if 'before' in args:
            cursor = args['before']
            if not isinstance(cursor, dict) or set(cursor) != {'created_at', 'id'}:
                raise ValueError('Invalid conversation cursor')
            if not isinstance(cursor['created_at'], str) or len(cursor['created_at']) > 40:
                raise ValueError('Invalid cursor timestamp')
            timestamp = datetime.fromisoformat(cursor['created_at'])
            if timestamp.utcoffset() is None:
                raise ValueError('Cursor timestamp must have a timezone')
            result['before'] = (timestamp, parse_uuid(cursor['id']))
        return result

    @staticmethod
    def dispatch(store, action, args):
        if action == 'create':
            return {'conversation': conversation_view(store.create(**args))}
        if action in ('get', 'last_active'):
            row = store.get(**args) if action == 'get' else store.last_active()
            return {'conversation': conversation_view(row) if row is not None else None}
        if action == 'delete':
            store.delete(**args)
            return {'deleted': True}
        if action == 'list':
            rows = store.list_conversations(**args)
            cursor = None
            if len(rows) == args['limit']:
                cursor = {'created_at': wire_value(rows[-1]['created_at']), 'id': str(rows[-1]['id'])}
            return {'conversations': [conversation_view(row) for row in rows], 'next_before': cursor}
        rows = store.messages(**args)
        cursor = rows[0]['sequence'] if len(rows) == args['limit'] else None
        return {'messages': [message_view(row) for row in rows], 'next_before_sequence': cursor}