"""Read-only validation of catalog coverage, hashes, source columns, and permissions."""

import json

import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row

from index_dataset_metadata import ROOT, SYSTEM_TABLES, content_hash, discover
from semantic_metadata import validate_document


def main():
    config = dotenv_values(ROOT / '.env', interpolate=False)
    with psycopg.connect(config['db_url'], sslmode='require', connect_timeout=10,
                         options='-c default_transaction_read_only=on -c statement_timeout=15000',
                         row_factory=dict_row) as connection:
        source = discover(connection)
        rows = connection.execute('''SELECT *, public.vector_dims(embedding) AS dimensions,
            public.vector_norm(embedding) AS norm FROM chatbot.dataset_metadata_embeddings
            ORDER BY dataset_table,metadata_type,chunk_index''').fetchall()
        grouped = {}
        for row in rows:
            assert row['dataset_table'] not in SYSTEM_TABLES
            assert row['dataset_schema'] == 'public'
            assert row['dimensions'] == 384 and abs(row['norm']-1) < 1e-5
            assert 0 < row['token_count'] <= 480
            assert row['content_hash'] == content_hash(row['original_text'], row['metadata_details'])
            grouped.setdefault(row['dataset_table'], []).append(row)
        assert set(grouped) == set(source), 'Missing or stale source datasets'
        for name, columns in source.items():
            entries = grouped[name]
            assert any(row['metadata_type']=='table_summary' for row in entries)
            column_entries = [row for row in entries if row['metadata_type']=='column_group']
            column_text = ''.join(row['original_text'].removeprefix(f'Dataset: public.{name}\n')
                                  for row in column_entries)
            for column in columns:
                assert f'{column["column_name"]}: {column["data_type"]};' in column_text
            assert entries[0]['metadata_details']['columns'] == columns
            semantic = entries[0]['metadata_details']['semantic_enrichment']
            validate_document(semantic['document'], columns)
            assert semantic['generator_model'] == 'openai/gpt-oss-120b'
        rls = connection.execute("SELECT relrowsecurity FROM pg_class WHERE oid='chatbot.dataset_metadata_embeddings'::regclass").fetchone()
        assert rls['relrowsecurity']
        for role in ('anon', 'authenticated'):
            if connection.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone():
                access = connection.execute("""SELECT has_schema_privilege(%s,'chatbot','USAGE') AS schema_access,
                    has_table_privilege(%s,'chatbot.dataset_metadata_embeddings','SELECT') AS table_access""",
                    (role, role)).fetchone()
                assert not access['schema_access'] and not access['table_access'], f'Unexpected access: {role}'
        result = {'verified_datasets': len(grouped), 'verified_chunks': len(rows),
                  'verified_source_columns': sum(len(columns) for columns in source.values()),
                  'metadata_types': {kind: sum(row['metadata_type']==kind for row in rows)
                                     for kind in ('table_summary','column_group')},
                  'max_input_tokens': max(row['token_count'] for row in rows),
                  'hashes_columns_vectors_permissions': 'PASS'}
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()