"""Versioned paraphrase benchmark. Source labels are agent-reviewed, not legal QA."""

import json

from dotenv import dotenv_values

from document_context import build_document_context
from metadata_embeddings import ROOT, Embedder
from retrieval import Retriever, read_connection


def relevance_metrics(hit_ids, expected_ids):
    hit_ids = hit_ids[:10]
    expected = set(expected_ids)
    if not expected:
        raise ValueError('Expected at least one labeled source')
    ranks = [rank for rank, value in enumerate(hit_ids, 1) if value in expected]
    return {'hit_at_5': any(rank <= 5 for rank in ranks), 'hit_at_10': bool(ranks),
            'reciprocal_rank_at_10': 1 / min(ranks) if ranks else 0,
            'labeled_recall_at_10': len(set(hit_ids) & expected) / len(expected)}


def main():
    try:
        fixture = json.loads((ROOT / 'document_relevance_cases.json').read_text(encoding='utf-8'))
        embedder = Embedder()
        cases, diagnostics = [], []
        with read_connection(dotenv_values(ROOT / '.env', interpolate=False)['db_url']) as conn:
            retriever = Retriever(conn, embedder)
            for case in fixture['positive_cases']:
                valid = True
                for expected in case['expected']:
                    row = conn.execute('''SELECT document_id,content_hash FROM chatbot.ready_document_chunks
                        WHERE id=%s''', (expected['id'],)).fetchone()
                    valid = valid and row is not None and row['document_id'] == case['document_id'] \
                        and row['content_hash'] == expected['hash']
                if not valid:
                    cases.append({'name': case['name'], 'labels_valid': False})
                    continue
                result = retriever.documents(case['question'], document_id=case['document_id'], limit=10)
                ids = [hit['id'] for hit in result['hits']]
                cases.append({'name': case['name'], 'labels_valid': True, 'hit_ids': ids,
                    **relevance_metrics(ids, [value['id'] for value in case['expected']])})
            for case in fixture['diagnostic_cases']:
                result = retriever.documents(case['question'])
                context = build_document_context(result)
                diagnostics.append({**case, 'candidate_count': len(result['hits']),
                    'context_status': context['status'], 'answerability_verified': False,
                    'answer_generated': context['answer_generated']})
        valid = all(case['labels_valid'] for case in cases)
        report = {'fixture_version': fixture['version'], 'label_origin': fixture['label_origin'],
            'labels_valid': valid, 'cases': cases, 'diagnostics': diagnostics,
            'hit_at_5_rate': sum(case.get('hit_at_5', False) for case in cases) / len(cases),
            'hit_at_10_rate': sum(case.get('hit_at_10', False) for case in cases) / len(cases),
            'mean_reciprocal_rank_at_10': sum(case.get('reciprocal_rank_at_10', 0) for case in cases) / len(cases),
            'all_labeled_cases_found': valid and all(case.get('hit_at_10') for case in cases),
            'groq_calls': 0, 'limitations': [
                'Small agent-labeled development set; not held-out or independently human-reviewed.',
                'Only known source passages are labeled; other hits may also be relevant.',
                'Missing labels fail validation instead of silently reassigning expected passages.',
                'Unsupported/current-law/ambiguous cases are diagnostics, not successful abstentions.',
                'No generated answer, entailment check, or current-law determination.']}
        output = ROOT / 'retrieval-output'
        output.mkdir(exist_ok=True)
        (output / 'relevance-evaluation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))
        # Relevance misses are visible release-gate failures, not hidden diagnostics.
        return 0 if report['all_labeled_cases_found'] else 1
    except Exception as error:
        print(f'Relevance evaluation failed ({type(error).__name__}); details withheld.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())