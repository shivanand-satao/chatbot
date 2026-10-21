"""Read-only coverage/privilege audit. No model loads, API calls, or migrations."""

import json
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parent
OPTIONS = '-c default_transaction_read_only=on -c statement_timeout=15000 -c lock_timeout=3000'


def dependencies():
    result = {}
    for name in ('psycopg', 'python-dotenv', 'groq', 'pydantic', 'transformers',
                 'torch', 'fastapi', 'uvicorn', 'PyJWT', 'sqlglot', 'psycopg-pool'):
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = None
    return result


def assess(report):
    blockers = []
    metadata = report['metadata']
    if not metadata['datasets'] or metadata['invalid_vectors']:
        blockers.append('Dataset metadata is absent or contains invalid vectors.')
    if report['missing_source_tables'] or report['column_drift_count']:
        blockers.append('Indexed dataset schema differs from current source schema.')
    if len(report['metadata_models']) != 1:
        blockers.append('Dataset model/revision is missing or mixed.')
    if not report['documents'] or any(d['ready'] != d['chunks'] or not d['chunks']
                                       for d in report['documents']):
        blockers.append('Full document retrieval coverage is incomplete.')
    if report['invalid_ready_chunks']:
        blockers.append('Ready document chunks fail source/vector consistency checks.')
    if report['document_models'] and {
            (m['embedding_model'], m['model_revision']) for m in report['document_models']
    } != {(m['embedding_model'], m['model_revision']) for m in report['metadata_models']}:
        blockers.append('Document and dataset embedding models/revisions differ.')
    role = report['role']
    if role['rolsuper'] or role['rolbypassrls'] or role['rolcreaterole'] or role['rolcreatedb']:
        blockers.append('Audited database role has elevated privileges; not an analytical runtime credential.')
    if any(t['can_write'] or t['owns_table'] for t in report['privileges']):
        blockers.append('Audited role can write to or owns tables; provision a restricted analytical role.')
    if any(t['schema'] == 'public' and t['table'] in ('users', 'admins', 'refresh_tokens')
           and t['can_read'] for t in report['privileges']):
        blockers.append('Audited role can read authentication tables; exclude these from analytical runtime access.')
    if any(p['schema_access'] or p['table_access'] for p in report['application_grants']):
        blockers.append('Application role has chatbot schema/table grants; review exposure and RLS before deployment.')
    return blockers


def collect(conn):
    if conn.execute('SHOW transaction_read_only').fetchone()['transaction_read_only'] != 'on':
        raise RuntimeError('Audit requires a read-only transaction')
    report = {'checked_at': datetime.now(timezone.utc).isoformat(),
              'dependencies': dependencies(), 'read_only': True}
    report['role'] = conn.execute('''SELECT rolsuper,rolbypassrls,rolcreaterole,rolcreatedb
        FROM pg_roles WHERE rolname=current_user''').fetchone()
    report['metadata'] = conn.execute('''SELECT count(*) AS vectors,
        count(DISTINCT (dataset_schema,dataset_table)) AS datasets,
        count(*) FILTER (WHERE public.vector_dims(embedding)<>384
            OR abs(public.vector_norm(embedding)-1)>0.00001) AS invalid_vectors
        FROM chatbot.dataset_metadata_embeddings''').fetchone()
    report['metadata_models'] = conn.execute('''SELECT embedding_model,model_revision,count(*) AS vectors
        FROM chatbot.dataset_metadata_embeddings GROUP BY 1,2 ORDER BY 1,2''').fetchall()
    report['missing_source_tables'] = conn.execute('''SELECT DISTINCT dataset_schema,dataset_table
        FROM chatbot.dataset_metadata_embeddings
        WHERE to_regclass(format('%I.%I',dataset_schema,dataset_table)) IS NULL''').fetchall()
    report['column_drift_count'] = conn.execute('''SELECT count(*) AS n FROM (
        SELECT DISTINCT dataset_schema,dataset_table,metadata_details->'columns' AS columns
        FROM chatbot.dataset_metadata_embeddings) m
        CROSS JOIN LATERAL jsonb_array_elements(m.columns) col
        WHERE NOT EXISTS (SELECT 1 FROM information_schema.columns c
            WHERE c.table_schema=m.dataset_schema AND c.table_name=m.dataset_table
            AND c.column_name=col->>'column_name' AND c.data_type=col->>'data_type')''').fetchone()['n']
    report['documents'] = conn.execute('''SELECT d.id,d.filename,d.version_label,d.page_count,
        count(c.id) AS chunks,count(c.id) FILTER (WHERE c.status='ready'
            AND c.content_hash=c.embedded_content_hash) AS ready,
        count(c.id) FILTER (WHERE c.status='pending') AS pending,
        count(c.id) FILTER (WHERE c.status='review') AS review,
        count(c.id) FILTER (WHERE c.status='failed') AS failed,
        count(c.id) FILTER (WHERE c.status='retired') AS retired
        FROM chatbot.documents d LEFT JOIN chatbot.document_chunks c ON c.document_id=d.id
        GROUP BY d.id ORDER BY d.id''').fetchall()
    # Count page coverage separately to avoid multiplying chunk totals by pages.
    for document in report['documents']:
        document['pages_with_ready_passages'] = conn.execute('''SELECT count(*) AS n
            FROM chatbot.document_pages p WHERE p.document_id=%s AND EXISTS (
                SELECT 1 FROM chatbot.ready_document_chunks r WHERE r.document_id=p.document_id
                AND p.page_number BETWEEN r.page_start AND r.page_end)''', (document['id'],)).fetchone()['n']
    report['document_models'] = conn.execute('''SELECT embedding_model,model_revision,count(*) AS vectors
        FROM chatbot.ready_document_chunks GROUP BY 1,2 ORDER BY 1,2''').fetchall()
    report['invalid_ready_chunks'] = conn.execute('''SELECT count(*) AS n FROM chatbot.document_chunks
        WHERE status='ready' AND (embedding IS NULL OR embedding_text IS NULL
        OR semantic_metadata IS NULL OR embedded_content_hash IS DISTINCT FROM content_hash
        OR public.vector_dims(embedding)<>384 OR abs(public.vector_norm(embedding)-1)>0.00001
        OR position(original_text in embedding_text)=0)''').fetchone()['n']
    report['privileges'] = conn.execute('''SELECT n.nspname AS schema,c.relname AS table,
        c.relrowsecurity AS rls,c.relforcerowsecurity AS force_rls,
        c.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user) AS owns_table,
        has_table_privilege(c.oid,'SELECT') AS can_read,
        has_table_privilege(c.oid,'INSERT,UPDATE,DELETE,TRUNCATE') AS can_write
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname IN ('public','chatbot') AND c.relkind IN ('r','v','p')
        ORDER BY 1,2''').fetchall()
    report['application_grants'] = conn.execute('''SELECT r.rolname AS role,c.relname AS table,
        has_schema_privilege(r.oid,n.oid,'USAGE') AS schema_access,
        has_table_privilege(r.oid,c.oid,'SELECT') AS table_access
        FROM pg_roles r CROSS JOIN pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE r.rolname IN ('anon','authenticated') AND n.nspname='chatbot'
        AND c.relkind IN ('r','v','p') ORDER BY 1,2''').fetchall()
    report['blockers'] = assess(report)
    report['scope_note'] = ('Audits the configured indexing connection, not a provisioned runtime role. '
                            'Privilege checks are preliminary; function/schema/role-membership review '
                            'and runtime identity isolation tests remain required. No LLM or authentication calls made.')
    return report


def main():
    config = dotenv_values(ROOT / '.env', interpolate=False)
    if not config.get('db_url'):
        print('Audit unavailable: db_url is not configured. No secret values printed.')
        return 1
    try:
        with psycopg.connect(config['db_url'], sslmode='require', connect_timeout=10,
                             options=OPTIONS, row_factory=dict_row) as conn:
            # Transaction poolers may not preserve connection startup settings.
            conn.execute('SET TRANSACTION READ ONLY')
            conn.execute("SET LOCAL statement_timeout='15000ms'")
            conn.execute("SET LOCAL lock_timeout='3000ms'")
            report = collect(conn)
            conn.rollback()
    except Exception as error:
        # Driver errors may contain connection credentials; deliberately omit their text.
        print(f'Audit failed ({type(error).__name__}); connection/query details withheld.')
        return 1
    output = ROOT / 'readiness-output'
    output.mkdir(exist_ok=True)
    (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items()
                      if key not in ('privileges', 'application_grants')}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())