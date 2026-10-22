"""Explicit additive grants to the reviewed 78-table catalog; no source row writes."""

import secrets
from pathlib import Path

import psycopg
from dotenv import dotenv_values, set_key
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from analytics import CATALOG, ROLE, analytics_connection, execute


def main():
    env = Path(__file__).parent / '.env'
    config = dotenv_values(env, interpolate=False)
    if config.get('analytics_db_url'):
        raise RuntimeError('Existing credential; refusing replacement')
    password = secrets.token_urlsafe(48)
    params = conninfo_to_dict(config['db_url'])
    suffix = params['user'].partition('.')[2]
    params.update(user=ROLE + ('.' + suffix if suffix else ''), password=password)
    runtime = make_conninfo(**params)
    with psycopg.connect(config['db_url'], sslmode='require', connect_timeout=8) as db:
        if db.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (ROLE,)).fetchone():
            raise RuntimeError('Role exists; inspect before retrying')
        db.execute("SET LOCAL statement_timeout='15000ms'")
        db.execute("SET LOCAL lock_timeout='2000ms'")
        db.execute(sql.SQL('CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
                           'NOINHERIT NOREPLICATION NOBYPASSRLS').format(sql.Identifier(ROLE)))
        db.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(ROLE)))
        for table, schema in CATALOG.items():
            actual = dict(db.execute('''SELECT column_name,data_type FROM information_schema.columns
                WHERE table_schema='public' AND table_name=%s''', (table,)).fetchall())
            if actual != schema['columns']:
                raise RuntimeError('Schema drift; no grants committed')
            kind, rls = db.execute('''SELECT c.relkind,c.relrowsecurity FROM pg_class c
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relname=%s''', (table,)).fetchone()
            if kind != 'r':
                raise RuntimeError('Only approved base tables allowed')
            db.execute(sql.SQL('GRANT SELECT ON {} TO {}').format(
                sql.Identifier('public', table), sql.Identifier(ROLE)))
            if rls:
                db.execute(sql.SQL('CREATE POLICY chatbot_analytics_read ON {} FOR SELECT TO {} USING (true)').format(
                    sql.Identifier('public', table), sql.Identifier(ROLE)))
        db.execute(sql.SQL('ALTER ROLE {} SET default_transaction_read_only=on').format(sql.Identifier(ROLE)))
        db.execute(sql.SQL("ALTER ROLE {} SET statement_timeout='5s'").format(sql.Identifier(ROLE)))
        db.execute(sql.SQL("ALTER ROLE {} SET lock_timeout='1s'").format(sql.Identifier(ROLE)))
        db.execute(sql.SQL('ALTER ROLE {} SET search_path=pg_catalog,public').format(sql.Identifier(ROLE)))
        db.execute(sql.SQL('ALTER ROLE {} LOGIN PASSWORD {} CONNECTION LIMIT 3').format(
            sql.Identifier(ROLE), sql.Literal(password)))
    # Save only after commit. A failed save requires inspection, never automatic role replacement.
    set_key(str(env), 'analytics_db_url', runtime, quote_mode='always')
    with analytics_connection(runtime) as db:
        exposed = db.execute('''SELECT n.nspname,c.relname FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname IN ('public','chatbot','auth') AND c.relkind IN ('r','v','m','p','f')
            AND has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')''').fetchall()
        if any(r['nspname'] != 'public' or r['relname'] not in CATALOG for r in exposed):
            raise RuntimeError('Unexpected privileges; do not deploy')
        writable = db.execute('''SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname IN ('public','chatbot','auth') AND c.relkind IN ('r','p')
            AND (has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER')
                 OR has_any_column_privilege(current_user,c.oid,'INSERT,UPDATE'))''').fetchall()
        if writable:
            raise RuntimeError('Write privileges found; do not deploy')
        table = next(iter(CATALOG))
        result = execute(db, {'table': table, 'columns': ['year'], 'limit': 1}, table)
        if not result['rows']:
            raise RuntimeError('Expected analytical rows')
    print('Restricted analytics role verified; only approved analytical tables readable; no source rows changed.')
    print('Runtime credential saved in ignored .env; never upload db_url or history_db_url.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Stopped:', type(error).__name__, '; details withheld. Inspect state before retry.')
        raise SystemExit(1)