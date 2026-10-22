"""Resume existing pending book chunks without extraction or schema migrations.

Operational command: makes billable Groq calls and writes document vectors.
Progress is stored in ignored book-output/resume-status.json. Errors are
sanitized; never serialize exception bodies that might contain credentials.
"""

import json
import os
from datetime import datetime, timezone

import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row

from book_pipeline import OUTPUT, enrich, report
from metadata_embeddings import ROOT, Embedder


def counts(connection):
    return {row['status']: row['n'] for row in connection.execute(
        'SELECT status,count(*) AS n FROM chatbot.document_chunks GROUP BY status').fetchall()}


def checkpoint(state, **details):
    OUTPUT.mkdir(exist_ok=True)
    path = OUTPUT / 'resume-status.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'state': state, 'pid': os.getpid(),
        'updated_at': datetime.now(timezone.utc).isoformat(), **details}, indent=2), encoding='utf-8')
    temporary.replace(path)


def main():
    checkpoint('starting')
    try:
        config = dotenv_values(ROOT / '.env', interpolate=False)
        with psycopg.connect(config['db_url'], sslmode='require', connect_timeout=10,
                             autocommit=True, row_factory=dict_row) as connection:
            if not connection.execute('SELECT pg_try_advisory_lock(738124,2) AS ok').fetchone()['ok']:
                raise RuntimeError('Another book pipeline holds the advisory lock')
            connection.execute("SET statement_timeout='30s'")
            initial = counts(connection)
            checkpoint('loading_local_model', initial=initial, counts=initial)
            embedder = Embedder()
            while True:
                before = counts(connection)
                if not before.get('pending', 0):
                    break
                checkpoint('running', initial=initial, counts=before)
                enrich(connection, embedder, config['groq_api'], limit=100)
                after = counts(connection)
                report(connection)
                checkpoint('running', initial=initial, counts=after)
                if after.get('pending', 0) >= before.get('pending', 0):
                    raise RuntimeError('No pending-chunk progress; manual review required')
            # Read-only validation: do not edit source passages even temporarily.
            validation = connection.execute('''SELECT count(*) AS ready,
                count(*) FILTER (WHERE embedding IS NULL OR public.vector_dims(embedding)<>384
                    OR abs(public.vector_norm(embedding)-1)>0.00001
                    OR embedding_text IS NULL OR position(original_text in embedding_text)=0
                    OR embedded_content_hash IS DISTINCT FROM content_hash
                    OR semantic_metadata IS NULL) AS invalid
                FROM chatbot.document_chunks WHERE status='ready' ''').fetchone()
            if validation['invalid']:
                raise RuntimeError('Ready-vector validation failed')
            final = counts(connection)
            report(connection)
            state = 'complete' if set(final) <= {'ready', 'retired'} and validation['ready'] else 'needs_review'
            checkpoint(state, initial=initial, counts=final, validation=validation)
            return 0 if state == 'complete' else 2
    except Exception as error:
        checkpoint('failed', error_type=type(error).__name__,
                   http_status=getattr(error, 'status_code', None),
                   note='Completed chunks are retained. Inspect counts before resuming; no automatic restart.')
        print('Indexing stopped:', type(error).__name__, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())