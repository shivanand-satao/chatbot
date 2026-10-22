"""Explicit, additive history provisioning. Never drops existing objects or data."""

import argparse
import secrets

import psycopg
from dotenv import dotenv_values, set_key
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row

from history_runtime import ROLE, verify_role
from metadata_embeddings import ROOT


def migration_body(name):
    lines = (ROOT / 'migrations' / name).read_text(encoding='utf-8').splitlines()
    return '\n'.join(line for line in lines if line.strip() not in ('BEGIN;', 'COMMIT;'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', required=True)
    parser.parse_args()
    config = dotenv_values(ROOT / '.env', interpolate=False)
    if config.get('history_db_url'):
        raise RuntimeError('Existing history credential found; refusing replacement')
    with psycopg.connect(config['db_url'], sslmode='require', connect_timeout=10,
                         autocommit=True, row_factory=dict_row) as admin:
        existing = admin.execute('''SELECT to_regclass('chatbot.conversations') AS conversations,
            to_regclass('chatbot.messages') AS messages,
            to_regclass('chatbot.conversation_requests') AS requests''').fetchone()
        if any(existing.values()) or admin.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (ROLE,)).fetchone():
            raise RuntimeError('History objects already exist; refusing blind migration rerun')
        for rehearsal in (True, False):
            with admin.transaction(force_rollback=rehearsal):
                admin.execute("SET LOCAL lock_timeout='3s'")
                admin.execute("SET LOCAL statement_timeout='15s'")
                admin.execute(migration_body('003_conversations.sql'))
                admin.execute(migration_body('004_history_runtime.sql'))
            print('Migration rollback rehearsal passed.' if rehearsal else 'Migrations 003 and 004 committed.', flush=True)
        password = secrets.token_urlsafe(48)
        dsn = conninfo_to_dict(config['db_url'])
        suffix = dsn['user'].partition('.')[2]
        dsn['user'] = ROLE + ('.' + suffix if suffix else '')
        dsn['password'] = password
        runtime = make_conninfo(**dsn)
        # Persist before enabling login so an interrupted provisioning is recoverable.
        set_key(str(ROOT / '.env'), 'history_db_url', runtime, quote_mode='always')
        admin.execute(sql.SQL('ALTER ROLE {} LOGIN PASSWORD {} CONNECTION LIMIT 8').format(
            sql.Identifier(ROLE), sql.Literal(password)))
    with psycopg.connect(runtime, sslmode='require', connect_timeout=10,
                         autocommit=True, row_factory=dict_row) as connection:
        verify_role(connection)
        print('Dedicated history login verified. Secret saved only in ignored .env.', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Provisioning stopped:', type(error).__name__, '; details withheld. Inspect state before retrying.')
        raise SystemExit(1)