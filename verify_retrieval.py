"""Small live retrieval evaluation using cached embeddings, never Groq."""

import json

from dotenv import dotenv_values

from metadata_embeddings import ROOT, Embedder
from retrieval import Retriever, read_connection

CASES = [
    ('auto theft motor vehicles', 'auto_theft'),
    ('serious fraud loss of property', 'serious_fraud'),
    ('property stolen and recovered', 'property_stolen'),
    ('वाहन चोरी', 'auto_theft'),
]


def main():
    try:
        config = dotenv_values(ROOT / '.env', interpolate=False)
        embedder = Embedder()
        checks = []
        with read_connection(config['db_url']) as conn:
            retriever = Retriever(conn, embedder)
            for question, expected in CASES:
                result = retriever.datasets(question)
                tables = [hit['dataset_table'] for hit in result['hits']]
                checks.append({'question': question, 'expected_name_fragment': expected,
                               'tables': tables, 'passed': any(expected in name for name in tables)})
            unavailable = conn.execute('''SELECT d.id FROM chatbot.documents d
                WHERE NOT EXISTS (SELECT 1 FROM chatbot.ready_document_chunks c
                    WHERE c.document_id=d.id AND c.embedding_model=%s AND c.model_revision=%s)
                ORDER BY d.id LIMIT 1''', ('intfloat/multilingual-e5-small', embedder.revision)).fetchone()
            if unavailable:
                result = retriever.documents('Explain this document', document_id=unavailable['id'])
                checks.append({'check': 'unavailable document returns no ready evidence',
                               'passed': result['status'] == 'no_ready_evidence' and not result['hits']})
            seed = conn.execute('''SELECT id,document_id,original_text,section_reference
                FROM chatbot.ready_document_chunks WHERE embedding_model=%s AND model_revision=%s
                ORDER BY id LIMIT 1''', ('intfloat/multilingual-e5-small', embedder.revision)).fetchone()
            if seed:
                result = retriever.documents(seed['original_text'][:300], document_id=seed['document_id'],
                                             section=seed['section_reference'] or None)
                checks.append({'check': 'ready passage self-retrieval (smoke test, not QA quality)',
                               'expected_chunk': seed['id'],
                               'retrieved_chunks': [h['id'] for h in result['hits']],
                               'passed': any(h['id'] == seed['id'] for h in result['hits'])})
            else:
                checks.append({'check': 'ready document evidence available', 'passed': False})
        report = {'model_revision': embedder.revision, 'checks': checks,
                  'passed': all(check['passed'] for check in checks), 'groq_calls': 0,
                  'limitation': 'Small discovery/smoke suite; not legal QA or SQL accuracy evaluation.'}
        output = ROOT / 'retrieval-output'
        output.mkdir(exist_ok=True)
        (output / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report['passed'] else 1
    except Exception as error:
        print(f'Verification failed ({type(error).__name__}); connection/query details withheld.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())