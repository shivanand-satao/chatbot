"""Read-only index-plan and ANN recall smoke checks; CPU only, no Groq."""

import json

from dotenv import dotenv_values

from metadata_embeddings import ROOT, Embedder
from retrieval import Retriever, read_connection


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
                ids = {row['id'] for row in approximate}
                recall = len(ids & {row['id'] for row in exact}) / max(len(exact), 1)
                indexed = 'document_chunks_cosine_idx' in json.dumps(plan)
                checks.append({'question': question, 'hnsw_plan': indexed,
                    'candidate_count': len(approximate), 'recall_at_100': recall,
                    'hits': [hit['id'] for hit in result['hits']],
                    'passed': indexed and recall >= .9 and bool(result['hits'])})
        report = {'checks': checks, 'passed': all(check['passed'] for check in checks),
                  'groq_calls': 0, 'limitation': 'Three unscoped smoke queries, not legal QA or general recall evaluation.'}
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