import copy
import unittest

from document_context import build_document_context, validate_cited_response


def evidence():
    return {'kind': 'documents', 'partial_coverage': True, 'limitations': ['Limited coverage'],
            'hits': [{'id': 1, 'document_id': 1, 'version_label': 'Uploaded edition',
                      'source_id': 'document:1:chunk:1:revision:1', 'revision': 1,
                      'title': 'Source', 'filename': 'source.pdf', 'page_start': 2,
                      'page_end': 2, 'section_reference': '1.', 'content_hash': 'hash',
                      'original_text': 'A source passage, not a generated answer.'}]}


class DocumentContextTests(unittest.TestCase):
    def test_preserves_original_passages_and_provenance(self):
        result = evidence()
        before = copy.deepcopy(result)
        context = build_document_context(result)
        self.assertEqual(context['status'], 'candidate_context')
        self.assertEqual(context['sources'][0]['original_text'], result['hits'][0]['original_text'])
        self.assertTrue(context['partial_coverage'])
        self.assertFalse(context['answer_generated'])
        self.assertEqual(result, before)

    def test_empty_evidence_remains_empty(self):
        result = evidence()
        result['hits'] = []
        self.assertEqual(build_document_context(result)['status'], 'no_ready_evidence')

    def test_candidate_context_never_authorizes_generation(self):
        context = build_document_context(evidence())
        self.assertEqual(context['status'], 'candidate_context')
        self.assertEqual(context['answerability']['status'], 'unverified')
        self.assertFalse(context['answerability']['generation_allowed'])
        self.assertFalse(context['answerability']['relevance_verified'])
        self.assertFalse(context['answerability']['current_law_verified'])

    def test_empty_context_remains_generation_blocked(self):
        result = evidence()
        result['hits'] = []
        self.assertFalse(build_document_context(result)['answerability']['generation_allowed'])

    def test_document_mixing_requires_selection(self):
        result = evidence()
        other = dict(result['hits'][0], document_id=2)
        result['hits'].append(other)
        context = build_document_context(result)
        self.assertEqual(context['status'], 'document_selection_required')
        self.assertEqual(context['sources'], [])

    def test_budget_omits_complete_passage_without_truncation(self):
        result = evidence()
        result['hits'][0]['original_text'] = 'x' * 2000
        context = build_document_context(result, 500)
        self.assertEqual(context['status'], 'context_budget_exceeded')
        self.assertEqual(context['context'], '')
        self.assertEqual(len(context['omitted_source_ids']), 1)

    def test_deduplicates_sources(self):
        result = evidence()
        result['hits'] *= 2
        self.assertEqual(len(build_document_context(result)['sources']), 1)

    def test_rejects_invalid_source_ids_and_page_ranges(self):
        for key, value in [('source_id', 'invented'), ('page_start', 0), ('page_end', 1)]:
            result = evidence()
            result['hits'][0][key] = value
            with self.assertRaises(ValueError):
                build_document_context(result)

    def test_budget_validation(self):
        for budget in (0, True, 50001):
            with self.assertRaises(ValueError):
                build_document_context(evidence(), budget)

    def test_validates_citation_identity_not_factual_correctness(self):
        context = build_document_context(evidence())
        response = {'claims': [{'text': 'An unverified generated claim',
                               'source_ids': [context['sources'][0]['source_id']]}]}
        result = validate_cited_response(response, context)
        self.assertTrue(result['citation_identity_valid'])
        self.assertFalse(result['factual_support_verified'])

    def test_rejects_unknown_duplicate_and_missing_citations(self):
        context = build_document_context(evidence())
        source = context['sources'][0]['source_id']
        for ids in ([], ['invented'], [source, source], [None]):
            with self.assertRaises(ValueError):
                validate_cited_response({'claims': [{'text': 'Claim', 'source_ids': ids}]}, context)

    def test_no_answer_without_context(self):
        with self.assertRaises(ValueError):
            validate_cited_response({'claims': []}, {'status': 'no_ready_evidence'})

    def test_rejects_extra_response_fields_and_uncited_prose(self):
        context = build_document_context(evidence())
        with self.assertRaises(ValueError):
            validate_cited_response({'claims': [], 'answer': 'Uncited prose'}, context)


if __name__ == '__main__':
    unittest.main()