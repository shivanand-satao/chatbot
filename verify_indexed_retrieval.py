"""Read-only index-plan and ANN recall smoke checks; CPU only, no Groq."""

import json

from dotenv import dotenv_values

from metadata_embeddings import ROOT, Embedder
from retrieval import Retriever, read_connection


def candidate_recall(approximate, exact):
    expected = {row['id'] for row in exact}
    if not expected:
        return 1.0 if not approximate else 0.0
    return len({row['id'] for row in approximate} & expected) / len(expected)


def compare_candidates(conn, sql, params):
    with conn.transaction():
        conn.execute('SET LOCAL enable_seqscan=off')
        conn.execute('SET LOCAL enable_sort=off')
        plan = conn.execute('EXPLAIN (FORMAT JSON) ' + sql, params).fetchone()['QUERY PLAN']
        approximate = conn.execute(sql, params).fetchall()
        conn.execute('SET LOCAL enable_seqscan=on')
        conn.execute('SET LOCAL enable_sort=on')
        conn.execute('SET LOCAL enable_indexscan=off')
        conn.execute('SET LOCAL enable_bitmapscan=off')
        exact = conn.execute(sql, params).fetchall()
        conn.execute('SET LOCAL enable_indexscan=on')
        conn.execute('SET LOCAL enable_bitmapscan=on')
    return {'hnsw_plan': 'document_chunks_cosine_idx' in json.dumps(plan),
            'candidate_count': len(approximate), 'exact_count': len(exact),
            'recall_at_100': candidate_recall(approximate, exact)}


class ObservedConnection:
    def __init__(self, connection):
        self.connection = connection
        self.search = None

    def execute(self, sql, params=None):
        if 'ORDER BY c.embedding <=>' in sql:
            self.search = (sql, params)
        return self.connection.execute(sql, params)


def main():
    try:
        config = dotenv_values(ROOT / '.env', interpolate=False)
        embedder = Embedder()
        checks = []
        with read_connection(config['db_url']) as conn:
            observed = ObservedConnection(conn)
            retriever = Retriever(observed, embedder)
            for question in ('criminal procedure arrest', 'constitutional safeguards', 'चोरी की सजा'):
                result = retriever.documents(question)
                sql, params = observed.search
                measured = compare_candidates(conn, sql, params)
                checks.append({'question': question, **measured,
                    'hits': [hit['id'] for hit in result['hits']],
                    'passed': measured['hnsw_plan'] and measured['recall_at_100'] >= .9 and bool(result['hits'])})
            fixture = json.loads((ROOT / 'document_relevance_cases.json').read_text(encoding='utf-8'))
            for case in fixture['positive_cases']:
                seed = conn.execute('''SELECT c.section_reference,d.version_label
                    FROM chatbot.ready_document_chunks c JOIN chatbot.documents d ON d.id=c.document_id
                    WHERE c.id=%s AND c.document_id=%s AND c.content_hash=%s''',
                    (case['expected'][0]['id'], case['document_id'], case['expected'][0]['hash'])).fetchone()
                if not seed:
                    checks.append({'case': case['name'], 'passed': False, 'reason': 'stale_label'})
                    continue
                for scope, filters in (
                    ('document', {'document_id': case['document_id']}),
                    ('version', {'version_label': seed['version_label']}),
                    ('section', {'document_id': case['document_id'], 'section': seed['section_reference']})):
                    result = retriever.documents(case['question'], **filters)
                    sql, params = observed.search
                    measured = compare_candidates(conn, sql, params)
                    checks.append({'case': case['name'], 'scope': scope, **measured,
                        'passed': measured['recall_at_100'] >= .9 and bool(result['hits'])})
        report = {'checks': checks, 'passed': all(check['passed'] for check in checks),
                  'groq_calls': 0, 'limitation': 'Development queries and labeled scopes; no legal QA or held-out evaluation. Selective filters may use exact index/sort plans.'}
        output = ROOT / 'retrieval-output'
        output.mkdir(exist_ok=True)
        (output / 'indexed-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report['passed'] else 1
    except Exception as error:
        print(f'Indexed verification failed ({type(error).__name__}); details withheld.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())