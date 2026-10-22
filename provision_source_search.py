"""Explicit one-time additive source-search role/index provisioning; no data changes."""

import secrets

import psycopg
from dotenv import dotenv_values, set_key
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from metadata_embeddings import ROOT
from source_search import ROLE, search_connection, search


def main():
    env = ROOT / '.env'
    config = dotenv_values(env, interpolate=False)
    if config.get('retrieval_db_url'):
        raise RuntimeError('Existing retrieval credential; refusing replacement')
    with psycopg.connect(config['db_url'], sslmode='require', connect_timeout=10) as db:
        if db.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (ROLE,)).fetchone():
            raise RuntimeError('Role already exists; inspect before retry')
        db.execute("SET LOCAL statement_timeout='60000ms'")
        db.execute("SET LOCAL lock_timeout='3000ms'")
        db.execute((ROOT / 'migrations/005_source_search.sql').read_text())
        password = secrets.token_urlsafe(48)
        params = conninfo_to_dict(config['db_url'])
        suffix = params['user'].partition('.')[2]
        params.update(user=ROLE + ('.' + suffix if suffix else ''), password=password)
        runtime = make_conninfo(**params)
        # Preserve recoverable credentials privately before enabling the login.
        set_key(str(env), 'retrieval_db_url', runtime, quote_mode='always')
        db.execute(sql.SQL('ALTER ROLE {} LOGIN PASSWORD {} CONNECTION LIMIT 4').format(
            sql.Identifier(ROLE), sql.Literal(password)))
    with search_connection(runtime) as db:
        result = search(db, 'right freedom')
        assert result['hits']
        forbidden = db.execute('''SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname IN ('public','auth','chatbot') AND c.relkind IN ('r','p','v','m','f')
            AND NOT (n.nspname='chatbot' AND c.relname IN ('documents','document_chunks'))
            AND has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')''').fetchall()
        assert not forbidden
        print('Source-search role/index applied; restricted login verified; sample search passed.')
        print('Credential saved only in ignored local .env. Existing source/account/history rows unchanged.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Provisioning stopped:', type(error).__name__, '; details withheld. Inspect state before retry.')
        raise SystemExit(1)