"""Index crime dataset metadata only. Source reads and catalog writes use separate connections."""

import argparse
import hashlib
import json
import re
import time
from pathlib import Path

import psycopg
from dotenv import dotenv_values
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from metadata_embeddings import Embedder, MODEL, pack_text
from semantic_metadata import Enricher, enriched_text, export_documents

ROOT = Path(__file__).resolve().parent
SYSTEM_TABLES = {
    'admins', 'users', 'refresh_tokens', 'user_settings', 'notifications',
    'dataset_metadata', 'dataset_columns', 'views_log', 'search_logs',
    'admin_modification_logs', 'response', 'api_request_logs', 'upload_history',
    'chatbot_schema_catalog', 'chatbot_catalog',
}
GEOGRAPHY = {'area_name', 'state/ut', 'states/uts', 'state', 'state_ut', 'district'}


def content_hash(text, details):
    payload = json.dumps({'text': text, 'details': details}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def discover(connection):
    rows = connection.execute("""
        SELECT c.table_name, c.column_name, c.data_type, c.is_nullable,
               col_description(pc.oid, a.attnum) AS column_comment,
               obj_description(pc.oid, 'pg_class') AS table_comment
        FROM information_schema.columns c
        JOIN information_schema.tables t USING (table_schema, table_name)
        JOIN pg_namespace n ON n.nspname=c.table_schema
        JOIN pg_class pc ON pc.relnamespace=n.oid AND pc.relname=c.table_name
        JOIN pg_attribute a ON a.attrelid=pc.oid AND a.attname=c.column_name
        WHERE c.table_schema='public' AND t.table_type='BASE TABLE'
        ORDER BY c.table_name, c.ordinal_position
    """).fetchall()
    tables = {}
    for row in rows:
        name = row['table_name']
        if name not in SYSTEM_TABLES:
            tables.setdefault(name, []).append(dict(row))
    # Fail closed on newly discovered non-analytical tables, rather than embedding them.
    unknown = [name for name, columns in tables.items()
               if not any(c['column_name'].lower() in GEOGRAPHY for c in columns)]
    if unknown:
        raise ValueError(f"Tables need analytical classification before indexing: {unknown}")
    return tables


def table_metadata(connection, name, columns):
    identifier = sql.Identifier('public', name)
    fields = [c for c in columns if c['column_name'].lower() in
              GEOGRAPHY | {'year', 'group_name', 'sub_group_name', 'subgroup'}]
    projections = [sql.SQL('(SELECT count(*) FROM {}) AS row_count').format(identifier)]
    for index, column in enumerate(fields):
        field = sql.Identifier(column['column_name'])
        projections.append(sql.SQL(
            'ARRAY(SELECT DISTINCT {}::text FROM {} WHERE {} IS NOT NULL ORDER BY 1 LIMIT 41) AS {}'
        ).format(field, identifier, field, sql.Identifier(f'values_{index}')))
        if column['column_name'].lower() == 'year' and column['data_type'] in {'integer', 'bigint', 'smallint'}:
            projections.extend([
                sql.SQL('(SELECT min({}) FROM {}) AS {}').format(field, identifier, sql.Identifier(f'min_{index}')),
                sql.SQL('(SELECT max({}) FROM {}) AS {}').format(field, identifier, sql.Identifier(f'max_{index}')),
            ])
    # One round trip for all aggregate statistics; no individual records are read.
    stats = connection.execute(sql.SQL('SELECT ') + sql.SQL(', ').join(projections)).fetchone()
    count = stats['row_count']
    dimensions = {}
    for index, column in enumerate(fields):
        field = column['column_name']
        values = stats[f'values_{index}']
        dimensions[field] = {'values': values[:40],
                             'values_truncated': len(values) > 40}
        if field.lower() == 'year' and column['data_type'] in {'integer', 'bigint', 'smallint'}:
            dimensions[field]['range'] = {'minimum': stats[f'min_{index}'], 'maximum': stats[f'max_{index}']}
    relationships = connection.execute("""
        SELECT conname, pg_get_constraintdef(oid) AS definition
        FROM pg_constraint WHERE conrelid=to_regclass(%s) AND contype='f'
        ORDER BY conname
    """, (identifier.as_string(connection),)).fetchall()
    descriptions = connection.execute("""
        SELECT dataset_title, dataset_description, dataset_category, source_url
        FROM public.dataset_metadata WHERE table_name=%s ORDER BY id
    """, (name,)).fetchall()
    label = re.sub(r'^\d+(?:_\d+)?_', '', name).replace('_', ' ')
    limitations = (
        'Topic label is derived from the table name, not an authoritative definition. '
        'Units and metric meanings are unverified unless explicitly documented in comments. '
        'Dimension values are database values and may include aggregate areas/categories. '
        'Do not sum totals with their components. Parallel tables are not assumed equivalent. '
        'Year suffixes in table names do not establish actual year coverage.'
    )
    details = {'row_count': count, 'dimensions': dimensions,
               'relationships': relationships, 'descriptions': descriptions,
               'limitations': limitations, 'columns': columns}
    summary = '\n'.join([
        f'Topic label from table name: {label}', f'Verified row count: {count}',
        f'Database table comment: {columns[0]["table_comment"] or "not provided"}',
        'Existing descriptions: ' + json.dumps(descriptions, ensure_ascii=False),
        'Verified dimension values (up to 40 per dimension): ' + json.dumps(dimensions, ensure_ascii=False),
        'Verified foreign keys: ' + json.dumps(relationships, ensure_ascii=False),
        'Limitations: ' + limitations,
    ])
    column_text = '\n'.join(
        f'{c["column_name"]}: {c["data_type"]}; nullable={c["is_nullable"]}; '
        f'comment={c["column_comment"] or "not provided"}' for c in columns
    )
    return summary, column_text, details


def vector_literal(vector):
    return '[' + ','.join(str(value) for value in vector) + ']'


def run(args):
    config = dotenv_values(ROOT / '.env', interpolate=False)
    if not config.get('db_url'):
        raise ValueError('db_url is missing from .env')
    embedder = Embedder()
    enricher = Enricher(config.get('groq_api'), ROOT / 'metadata-output')
    documents = []
    export_path = ROOT / 'metadata-output/crime_schema_summary.json'
    if export_path.exists():
        documents = json.loads(export_path.read_text(encoding='utf-8'))
    started = time.perf_counter()
    counts = {'datasets': 0, 'inserted_or_updated': 0, 'unchanged': 0, 'obsolete_chunks_removed': 0}
    with psycopg.connect(config['db_url'], sslmode='require', connect_timeout=10,
                         autocommit=True, row_factory=dict_row) as writer:
        locked = writer.execute("SELECT pg_try_advisory_lock(738124, 1) AS locked").fetchone()['locked']
        if not locked:
            raise RuntimeError('Another metadata indexer is already running')
        writer.execute("SET statement_timeout='30s'")
        with writer.transaction():
            writer.execute((ROOT / 'migrations/001_dataset_metadata_embeddings.sql').read_text())
            # Supabase roles must not inherit access through default grants.
            for role in ('anon', 'authenticated'):
                if writer.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone():
                    writer.execute(sql.SQL('REVOKE ALL ON SCHEMA chatbot FROM {}').format(sql.Identifier(role)))
                    writer.execute(sql.SQL('REVOKE ALL ON chatbot.dataset_metadata_embeddings FROM {}').format(sql.Identifier(role)))
        with psycopg.connect(config['db_url'], sslmode='require', connect_timeout=10,
                             options='-c default_transaction_read_only=on -c statement_timeout=15000',
                             row_factory=dict_row) as source:
            source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            tables = discover(source)
            selected = tables
            if args.pending_only:
                completed = {row['dataset_table'] for row in writer.execute('''
                    SELECT DISTINCT dataset_table FROM chatbot.dataset_metadata_embeddings
                    WHERE metadata_details ? 'semantic_enrichment'
                ''').fetchall()}
                selected = {name: columns for name, columns in tables.items() if name not in completed}
            if args.limit:
                selected = dict(list(selected.items())[:args.limit])
            print(f'Discovered {len(tables)} analytical tables; source connection is read-only.', flush=True)
            for name, columns in selected.items():
                summary, column_text, details = table_metadata(source, name, columns)
                semantic = enricher.enrich(name, columns, details)
                details['semantic_enrichment'] = semantic
                summary, column_text = enriched_text(summary, columns, semantic)
                prefix = f'Dataset: public.{name}\n'
                entries = []
                for kind, text in [('table_summary', summary), ('column_group', column_text)]:
                    for index, chunk in enumerate(pack_text(text, prefix, embedder.token_count)):
                        entries.append({'kind': kind, 'index': index, 'text': chunk,
                                        'hash': content_hash(chunk, details)})
                existing = writer.execute('''
                    SELECT metadata_type, chunk_index, content_hash, embedding_model, model_revision
                    FROM chatbot.dataset_metadata_embeddings WHERE dataset_schema='public' AND dataset_table=%s
                ''', (name,)).fetchall()
                old = {(r['metadata_type'], r['chunk_index']): r for r in existing}
                changed = [entry for entry in entries if
                           (entry['kind'], entry['index']) not in old or
                           (old[(entry['kind'], entry['index'])]['content_hash'],
                            old[(entry['kind'], entry['index'])]['embedding_model'],
                            old[(entry['kind'], entry['index'])]['model_revision']) !=
                           (entry['hash'], MODEL, embedder.revision)]
                vectors = embedder.encode([entry['text'] for entry in changed])
                with writer.transaction():
                    for entry, vector in zip(changed, vectors):
                        writer.execute('''
                            INSERT INTO chatbot.dataset_metadata_embeddings
                            (dataset_schema,dataset_table,metadata_type,chunk_index,original_text,
                             metadata_details,embedding,embedding_model,model_revision,content_hash,token_count)
                            VALUES ('public',%s,%s,%s,%s,%s,%s::public.vector,%s,%s,%s,%s)
                            ON CONFLICT (dataset_schema,dataset_table,metadata_type,chunk_index) DO UPDATE SET
                            original_text=EXCLUDED.original_text,metadata_details=EXCLUDED.metadata_details,
                            embedding=EXCLUDED.embedding,embedding_model=EXCLUDED.embedding_model,
                            model_revision=EXCLUDED.model_revision,content_hash=EXCLUDED.content_hash,
                            token_count=EXCLUDED.token_count,updated_at=now()
                        ''', (name,entry['kind'],entry['index'],entry['text'],Jsonb(details),
                              vector_literal(vector),MODEL,embedder.revision,entry['hash'],
                              embedder.token_count(entry['text'])))
                    for kind in ('table_summary', 'column_group'):
                        n = sum(entry['kind'] == kind for entry in entries)
                        deleted = writer.execute('''DELETE FROM chatbot.dataset_metadata_embeddings
                            WHERE dataset_schema='public' AND dataset_table=%s
                            AND metadata_type=%s AND chunk_index >= %s''', (name,kind,n)).rowcount
                        counts['obsolete_chunks_removed'] += deleted
                counts['datasets'] += 1
                counts['inserted_or_updated'] += len(changed)
                counts['unchanged'] += len(entries)-len(changed)
                print(f'{name}: {len(entries)} chunks, {len(changed)} changed', flush=True)
                documents = [document for document in documents if document['table_name'] != name]
                documents.append({'table_name': name, 'metadata_details': details,
                                  'summary_text': summary, 'column_text': column_text})
                documents.sort(key=lambda document: document['table_name'])
                export_documents(ROOT / 'metadata-output', documents)
        stored = writer.execute('''SELECT count(*) AS entries, count(DISTINCT dataset_table) AS datasets,
            min(public.vector_dims(embedding)) AS min_dimensions,
            max(public.vector_dims(embedding)) AS max_dimensions,
            max(token_count) AS max_input_tokens,
            min(public.vector_norm(embedding)) AS min_norm,
            max(public.vector_norm(embedding)) AS max_norm
            FROM chatbot.dataset_metadata_embeddings''').fetchone()
        if stored['datasets'] != len(tables) or stored['min_dimensions'] != 384 or stored['max_dimensions'] != 384:
            raise RuntimeError(f'Catalog coverage/dimensions verification failed: {stored}')
        if abs(stored['min_norm']-1) > 1e-5 or abs(stored['max_norm']-1) > 1e-5:
            raise RuntimeError('Stored vectors are not normalized')
        retrieval = []
        for question, expected in [
            ('serious fraud loss of property', {'serious_fraud','31_serious_fraud'}),
            ('auto theft motor vehicles', {'auto_theft','30_auto_theft'}),
            ('police housing', {'police_housing'}),
        ]:
            vector = vector_literal(embedder.encode([question], query=True)[0])
            hits = writer.execute('''SELECT dataset_table,metadata_type,
                1-(embedding <=> %s::public.vector) AS similarity
                FROM chatbot.dataset_metadata_embeddings
                WHERE embedding_model=%s AND model_revision=%s
                ORDER BY embedding <=> %s::public.vector LIMIT 5''',
                (vector,MODEL,embedder.revision,vector)).fetchall()
            retrieval.append({'question': question, 'expected_in_top5': any(h['dataset_table'] in expected for h in hits), 'hits': hits})
        writer.execute('ANALYZE chatbot.dataset_metadata_embeddings')
        report = {**counts, 'stored': stored, 'model': MODEL, 'revision': embedder.revision,
                  'groq_calls': enricher.calls, 'semantic_cache_hits': enricher.cache_hits,
                  'seconds': round(time.perf_counter()-started, 2), 'retrieval_checks': retrieval}
        output = ROOT / 'metadata-output'
        output.mkdir(exist_ok=True)
        (output / 'last_index_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report,indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pending-only', action='store_true', help='Resume missing enrichment only; use default run to refresh changed evidence')
    parser.add_argument('--limit', type=int, help='Maximum datasets to enrich in this batch')
    run(parser.parse_args())