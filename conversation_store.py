"""Controlled history repository. Caller supplies verified identity and a service connection.

No environment access, network connection factory, migration runner, or LLM calls.
Use an idle autocommit psycopg connection with dict_row; each operation owns its
short transaction. Do not share a connection across concurrent requests.
"""

import json
from contextlib import contextmanager
from datetime import datetime
from uuid import UUID, uuid4

from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from conversation_context import Owner, positive, text


class ConversationConflict(ValueError):
    pass


def uuid_value(value):
    if not isinstance(value, UUID):
        raise ValueError('Expected a UUID')
    return value


def page_limit(value):
    if type(value) is not int or not 1 <= value <= 100:
        raise ValueError('Page limit must be 1–100')


def validate_sources(sources):
    if not isinstance(sources, list) or len(sources) > 20:
        raise ValueError('Expected at most 20 source records')
    allowed = {'source_id', 'title', 'version_label', 'page_start', 'page_end', 'dataset'}
    seen = set()
    for source in sources:
        if not isinstance(source, dict) or 'source_id' not in source or set(source) - allowed:
            raise ValueError('Unsupported source metadata')
        for key, value in source.items():
            if key in ('page_start', 'page_end'):
                positive(value)
            else:
                text(value, 500)
        if ('page_start' in source) != ('page_end' in source):
            raise ValueError('Source page bounds must be paired')
        if 'page_start' in source and source['page_start'] > source['page_end']:
            raise ValueError('Invalid source page range')
        if source['source_id'] in seen:
            raise ValueError('Duplicate source identifier')
        seen.add(source['source_id'])
    if len(json.dumps(sources, ensure_ascii=True).encode('utf-8')) > 16000:
        raise ValueError('Source metadata exceeds storage budget')


class ConversationStore:
    def __init__(self, connection, owner, database_owner_scope=False):
        if not isinstance(owner, Owner):
            raise ValueError('Expected a verified owner record')
        if connection.row_factory is not dict_row:
            raise ValueError('Repository requires psycopg dict_row')
        self.connection, self.owner = connection, owner
        if type(database_owner_scope) is not bool:
            raise ValueError('database_owner_scope must be boolean')
        self.database_owner_scope = database_owner_scope

    @contextmanager
    def transaction(self):
        if not self.connection.autocommit or self.connection.info.transaction_status != TransactionStatus.IDLE:
            raise RuntimeError('Repository requires an idle autocommit connection')
        with self.connection.transaction():
            self.connection.execute("SET LOCAL statement_timeout='10000ms'")
            self.connection.execute("SET LOCAL lock_timeout='3000ms'")
            if self.database_owner_scope:
                self.connection.execute('''SELECT set_config('app.history_owner_kind',%s,true),
                    set_config('app.history_owner_id',%s,true)''',
                    (self.owner.account_kind, str(self.owner.account_id)))
            yield

    def _owned(self, conversation_id, lock=False):
        uuid_value(conversation_id)
        query = '''SELECT * FROM chatbot.conversations
            WHERE id=%s AND owner_kind=%s AND owner_id=%s'''
        if lock:
            query += ' FOR UPDATE'
        row = self.connection.execute(query, (conversation_id, self.owner.account_kind,
                                              self.owner.account_id)).fetchone()
        if row is None:
            raise PermissionError('Conversation unavailable')
        return row

    def create(self, title='New conversation'):
        text(title, 200)
        with self.transaction():
            return self.connection.execute('''INSERT INTO chatbot.conversations
                (id,owner_kind,owner_id,title) VALUES (%s,%s,%s,%s) RETURNING *''',
                (uuid4(), self.owner.account_kind, self.owner.account_id, title)).fetchone()

    def list_conversations(self, limit=20, before=None):
        """Stable creation-order cursor; latest activity is selected separately for resume."""
        page_limit(limit)
        if before is not None:
            if (not isinstance(before, tuple) or len(before) != 2
                    or not isinstance(before[0], datetime) or before[0].utcoffset() is None):
                raise ValueError('Expected a timezone-aware timestamp/UUID cursor')
            uuid_value(before[1])
        with self.transaction():
            query = '''SELECT * FROM chatbot.conversations WHERE owner_kind=%s AND owner_id=%s'''
            params = [self.owner.account_kind, self.owner.account_id]
            if before is not None:
                query += ' AND (created_at,id)<(%s,%s)'
                params.extend(before)
            query += ' ORDER BY created_at DESC,id DESC LIMIT %s'
            return self.connection.execute(query, (*params, limit)).fetchall()

    def last_active(self):
        with self.transaction():
            return self.connection.execute('''SELECT * FROM chatbot.conversations
                WHERE owner_kind=%s AND owner_id=%s
                ORDER BY updated_at DESC,id DESC LIMIT 1''',
                (self.owner.account_kind, self.owner.account_id)).fetchone()

    def get(self, conversation_id):
        with self.transaction():
            return self._owned(conversation_id)

    def messages(self, conversation_id, limit=50, before_sequence=None):
        page_limit(limit)
        if before_sequence is not None:
            positive(before_sequence)
        with self.transaction():
            self._owned(conversation_id)
            rows = self.connection.execute('''SELECT * FROM chatbot.messages
                WHERE conversation_id=%s AND (%s::bigint IS NULL OR sequence<%s)
                ORDER BY sequence DESC LIMIT %s''',
                (conversation_id, before_sequence, before_sequence, limit)).fetchall()
            return list(reversed(rows))

    def delete(self, conversation_id):
        with self.transaction():
            self._owned(conversation_id, lock=True)
            self.connection.execute('DELETE FROM chatbot.conversations WHERE id=%s', (conversation_id,))

    def begin_turn(self, conversation_id, request_id, content, expected_revision):
        uuid_value(request_id)
        text(content, 20000)
        positive(expected_revision)
        with self.transaction():
            conversation = self._owned(conversation_id, lock=True)
            existing = self.connection.execute('''SELECT r.state,m.content,m.sequence
                FROM chatbot.conversation_requests r JOIN chatbot.messages m
                ON m.conversation_id=r.conversation_id AND m.request_id=r.request_id AND m.role='user'
                WHERE r.conversation_id=%s AND r.request_id=%s''',
                (conversation_id, request_id)).fetchone()
            if existing:
                if existing['content'] != content:
                    raise ConversationConflict('Request identifier reused with different content')
                return {'replayed': True, **existing}
            if conversation['revision'] != expected_revision:
                raise ConversationConflict('Conversation changed; reload before sending')
            pending = self.connection.execute('''SELECT 1 FROM chatbot.conversation_requests
                WHERE conversation_id=%s AND state='pending' LIMIT 1''', (conversation_id,)).fetchone()
            if pending:
                raise ConversationConflict('Another turn is pending')
            self.connection.execute('''INSERT INTO chatbot.conversation_requests
                (conversation_id,request_id,state) VALUES (%s,%s,'pending')''', (conversation_id, request_id))
            self.connection.execute('''INSERT INTO chatbot.messages
                (conversation_id,sequence,request_id,role,content,status)
                VALUES (%s,%s,%s,'user',%s,'complete')''',
                (conversation_id, conversation['next_sequence'], request_id, content))
            self._advance(conversation_id)
            return {'replayed': False, 'state': 'pending', 'sequence': conversation['next_sequence']}

    def _advance(self, conversation_id):
        self.connection.execute('''UPDATE chatbot.conversations SET next_sequence=next_sequence+1,
            revision=revision+1,updated_at=clock_timestamp() WHERE id=%s''', (conversation_id,))

    def finish_turn(self, conversation_id, request_id, content, sources=None, failed=False):
        """Called by trusted generation service only, not a client-writable answer endpoint."""
        uuid_value(request_id)
        text(content, 20000)
        if type(failed) is not bool:
            raise ValueError('failed must be boolean')
        sources = [] if sources is None else sources
        validate_sources(sources)
        if failed and sources:
            raise ValueError('Failed answers must not retain source claims')
        with self.transaction():
            conversation = self._owned(conversation_id, lock=True)
            request = self.connection.execute('''SELECT state FROM chatbot.conversation_requests
                WHERE conversation_id=%s AND request_id=%s''', (conversation_id, request_id)).fetchone()
            if request is None:
                raise ConversationConflict('Request unavailable')
            state = 'failed' if failed else 'complete'
            if request['state'] != 'pending':
                saved = self.connection.execute('''SELECT * FROM chatbot.messages
                    WHERE conversation_id=%s AND request_id=%s AND role='assistant' ''',
                    (conversation_id, request_id)).fetchone()
                if saved is None or saved['content'] != content or saved['sources'] != sources or saved['status'] != state:
                    raise ConversationConflict('Completed request cannot be overwritten')
                return {'replayed': True, 'sequence': saved['sequence'], 'state': state}
            self.connection.execute('''INSERT INTO chatbot.messages
                (conversation_id,sequence,request_id,role,content,status,sources)
                VALUES (%s,%s,%s,'assistant',%s,%s,%s)''',
                (conversation_id, conversation['next_sequence'], request_id, content, state, Jsonb(sources)))
            self.connection.execute('''UPDATE chatbot.conversation_requests SET state=%s,
                updated_at=clock_timestamp() WHERE conversation_id=%s AND request_id=%s''',
                (state, conversation_id, request_id))
            self._advance(conversation_id)
            return {'replayed': False, 'sequence': conversation['next_sequence'], 'state': state}