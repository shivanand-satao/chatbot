"""Dedicated history connection factory. Never falls back to indexing credentials."""

from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from conversation_store import ConversationStore


ROLE = 'chatbot_history_runtime'


def verify_role(connection):
    row = connection.execute('''SELECT current_user AS name,rolsuper,rolcreaterole,
        rolcreatedb,rolbypassrls,rolreplication FROM pg_roles WHERE rolname=current_user''').fetchone()
    if row['name'] != ROLE or any(row[key] for key in
            ('rolsuper', 'rolcreaterole', 'rolcreatedb', 'rolbypassrls', 'rolreplication')):
        raise RuntimeError('Dedicated restricted history role required')
    if connection.execute('SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=current_user)').fetchone():
        raise RuntimeError('History role must not inherit memberships')


def store_factory(dsn):
    if not isinstance(dsn, str) or not dsn.strip():
        raise ValueError('Explicit history database connection required')

    @contextmanager
    def scope(owner):
        with psycopg.connect(dsn, sslmode='require', connect_timeout=10,
                             autocommit=True, row_factory=dict_row) as connection:
            verify_role(connection)
            yield ConversationStore(connection, owner, database_owner_scope=True)

    return scope