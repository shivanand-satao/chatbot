"""Explicit focused history smoke check. Creates/deletes only its own test rows.

Reads one JSON login object from stdin, never writes credentials or tokens.
Uses the configured frontend HTTPS origin and dedicated history_db_url only.
"""

import http.client
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row
from psycopg.errors import InsufficientPrivilege

from conversation_context import Owner
from conversation_store import ConversationConflict
from history_runtime import store_factory, verify_role
from history_service import HistoryService
from metadata_embeddings import ROOT
from server_identity import ProfileVerifier


def main():
    login = json.loads(sys.stdin.readline())
    origin = dotenv_values(ROOT.parent / 'frontend' / '.env', interpolate=False)['VITE_API_URL'].rstrip('/')
    parsed = urlsplit(origin)
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.path:
        raise ValueError('Expected configured HTTPS backend origin')
    connection = http.client.HTTPSConnection(parsed.hostname, parsed.port, timeout=30)
    try:
        connection.request('POST', '/api/auth/login-user', body=json.dumps(login),
                           headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
        response = connection.getresponse()
        body = response.read(32769)
        if response.status != 200 or len(body) > 32768:
            raise RuntimeError('Test account login failed')
        payload = json.loads(body)
        authorization = 'Bearer ' + payload['accessToken']
    finally:
        connection.close()
    verifier = ProfileVerifier(origin, timeout=10)
    owner = verifier.verify(authorization)
    if owner.account_kind != 'user':
        raise RuntimeError('Expected normal test user')
    dsn = dotenv_values(ROOT / '.env', interpolate=False)['history_db_url']
    scope = store_factory(dsn)
    checks = []
    cid = None
    try:
        with psycopg.connect(dsn, sslmode='require', autocommit=True, row_factory=dict_row) as db:
            verify_role(db)
            forbidden = db.execute('''SELECT n.nspname,c.relname FROM pg_class c
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname IN ('public','auth','chatbot') AND c.relkind IN ('r','p','v','m','f')
                AND NOT (n.nspname='chatbot' AND c.relname IN ('conversations','messages','conversation_requests'))
                AND has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')''').fetchall()
            assert not forbidden, 'Unexpected unrelated table privileges'
            checks.append('dedicated role: no unrelated public/auth/chatbot table access')
        service = HistoryService(verifier, scope, enabled=True)
        status, data = service.handle('create', authorization, {'title': 'Synthetic history integration check'})
        assert status == 201
        from uuid import UUID
        cid = UUID(data['data']['conversation']['id'])
        status, data = service.handle('get', authorization, {'conversation_id': str(cid)})
        assert status == 200 and data['data']['conversation']['id'] == str(cid)
        checks.append('hosted verified user: create and reload through HistoryService')
        with scope(owner) as store:
            request_id = uuid4()
            first = store.begin_turn(cid, request_id, 'Synthetic test question; not a real legal request.', 1)
            assert not first['replayed']
            assert store.begin_turn(cid, request_id, 'Synthetic test question; not a real legal request.', 1)['replayed']
            store.finish_turn(cid, request_id, 'Synthetic stored reply; no model generation or legal advice.')
            assert store.finish_turn(cid, request_id, 'Synthetic stored reply; no model generation or legal advice.')['replayed']
        with scope(owner) as store:
            messages = store.messages(cid)
            assert [row['sequence'] for row in messages] == [1, 2]
            revision = store.get(cid)['revision']
        checks.append('turn save/reload, contiguous sequence and idempotent replay')
        # Same numeric ID, other namespace: no admin bypass or raw SQL visibility.
        with scope(Owner('admin', owner.account_id)) as foreign:
            try:
                foreign.get(cid)
            except PermissionError:
                pass
            else:
                raise AssertionError('Foreign namespace repository access')
            with foreign.transaction():
                for table in ('conversations', 'messages', 'conversation_requests'):
                    column = 'id' if table == 'conversations' else 'conversation_id'
                    assert not foreign.connection.execute(
                        f'SELECT * FROM chatbot.{table} WHERE {column}=%s', (cid,)).fetchall()
            try:
                with foreign.transaction():
                    foreign.connection.execute('''INSERT INTO chatbot.conversations
                        (id,owner_kind,owner_id,title) VALUES (%s,%s,%s,%s)''',
                        (uuid4(), owner.account_kind, owner.account_id, 'Forbidden synthetic insert'))
            except InsufficientPrivilege:
                pass
            else:
                raise AssertionError('Cross-owner insert was not denied')
        with psycopg.connect(dsn, sslmode='require', autocommit=True, row_factory=dict_row) as db:
            assert not db.execute('SELECT id FROM chatbot.conversations WHERE id=%s', (cid,)).fetchall()
        checks.append('RLS: cross-namespace/unset-owner reads hidden; cross-owner insert denied')

        def submit(_):
            with scope(owner) as store:
                rid = uuid4()
                try:
                    store.begin_turn(cid, rid, 'Synthetic concurrent request.', revision)
                    return rid
                except ConversationConflict:
                    return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            winners = [rid for rid in pool.map(submit, range(2)) if rid]
        assert len(winners) == 1
        with scope(owner) as store:
            store.finish_turn(cid, winners[0], 'Synthetic concurrency cleanup.', failed=True)
        checks.append('concurrent begin: exactly one winner; second request conflicts')
        with scope(owner) as store:
            store.delete(cid)
            with store.transaction():
                assert not store.connection.execute('SELECT 1 FROM chatbot.messages WHERE conversation_id=%s', (cid,)).fetchall()
        checks.append('owned deletion and no visible residual messages')
        cid = None
    finally:
        if cid is not None:
            with scope(owner) as store:
                store.delete(cid)
    print(json.dumps({'passed': True, 'checks': checks,
        'cleanup': 'Only newly created synthetic conversation deleted; existing data preserved.',
        'limitations': 'Focused live smoke checks, not full deletion-race/lease/recovery or deployment validation.'}, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('History live verification failed:', type(error).__name__, '; details withheld. Check synthetic-row cleanup.')
        raise SystemExit(1)