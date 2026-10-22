import unittest
from unittest.mock import Mock

from source_explanation import SourceExplainer, add_explanation, validate_explanation
from source_search import search


class ExplanationTests(unittest.TestCase):
    def setUp(self):
        self.hit = {'source_id': 'document:1:chunk:2:revision:1',
                    'passage': 'Every person has the protections described in this historical reference.'}
        self.point = {'text': 'The excerpt describes protections for every person.',
                      'source_id': self.hit['source_id'], 'quote': self.hit['passage']}

    def test_exact_quote_and_citation_required(self):
        self.assertEqual(validate_explanation({'points': [self.point]}, [self.hit])['points'], [self.point])
        for change in ({'source_id': 'invented'}, {'quote': 'This quote was invented and is not in the passage.'},
                       {'text': 'x' * 1801}, {'extra': 'field'}, {'kind': 'invented'}):
            with self.assertRaises(ValueError):
                validate_explanation({'points': [{**self.point, **change}]}, [self.hit])
        with self.assertRaises(ValueError):
            validate_explanation({'points': []}, [self.hit])

    def test_provider_failure_keeps_sources(self):
        explainer = Mock()
        explainer.explain.side_effect = RuntimeError('private provider details')
        result = add_explanation({'question': 'protections', 'hits': [self.hit]}, explainer)
        self.assertEqual(result['explanation_status'], 'provider_error')
        self.assertEqual(result['hits'], [self.hit])
        self.assertNotIn('private', str(result))

    def test_provider_result_is_validated_and_attached(self):
        import json
        explainer = SourceExplainer.__new__(SourceExplainer)
        explainer.model = 'test-model'
        explainer.client = Mock()
        choice = Mock(finish_reason='stop')
        choice.message.content = json.dumps({'points': [self.point]})
        explainer.client.chat.completions.create.return_value.choices = [choice]
        result = add_explanation({'question': 'protections', 'hits': [self.hit]}, explainer)
        self.assertEqual(result['explanation_status'], 'available')
        self.assertEqual(result['explanation']['points'], [self.point])
        choice.message.content = json.dumps({'points': [{**self.point, 'source_id': 'invented'}]})
        self.assertEqual(add_explanation({'question': 'x', 'hits': [self.hit]}, explainer)
                         ['explanation_status'], 'invalid_response')

    def test_evidence_ids_resolve_to_original_text(self):
        import json
        explainer = SourceExplainer.__new__(SourceExplainer)
        explainer.model = 'test-model'
        explainer.client = Mock()
        choice = Mock(finish_reason='stop')
        point = {'kind': 'answer', 'text': 'Source-based response.', 'evidence_id': 'E1'}
        choice.message.content = json.dumps({'points': [point]})
        explainer.client.chat.completions.create.return_value.choices = [choice]
        result = explainer.explain('protections', [self.hit])
        self.assertEqual(result['points'][0]['quote'], self.hit['passage'])
        self.assertNotIn('evidence_id', result['points'][0])
        choice.message.content = json.dumps({'points': [{**point, 'evidence_id': 'E999'}]})
        with self.assertRaises(ValueError):
            explainer.explain('protections', [self.hit])

    def test_no_matches_does_not_call_provider(self):
        explainer = Mock()
        self.assertEqual(add_explanation({'hits': []}, explainer)['explanation_status'], 'no_matches')
        explainer.explain.assert_not_called()

    def test_all_document_search_is_diversified_and_bounded(self):
        db = Mock()
        db.execute.return_value.fetchall.return_value = []
        result = search(db, 'freedom')
        sql, parameters = db.execute.call_args.args
        self.assertEqual(parameters, (['freedom'], [], ['freedom'], ['freedom'], None, None))
        self.assertIn('PARTITION BY c.document_id', sql)
        self.assertIn('document_rank <= 3', sql)
        self.assertIn('LIMIT 10', sql)
        self.assertEqual(result['search_scope'], 'all_documents')

    def test_misspelled_arrest_query_requires_core_concept(self):
        from source_search import retrieval_terms
        question = 'now tell me what i can do if police arreest me wiihtou any notice'
        self.assertEqual(retrieval_terms(question), (['police', 'arrest', 'notice'], ['arrest']))
        db = Mock()
        db.execute.return_value.fetchall.return_value = []
        search(db, question)
        sql, params = db.execute.call_args.args
        self.assertEqual(params[1], ['arrest'])
        self.assertIn('coverage DESC,score DESC', sql)
        self.assertIn("NOT ILIKE '%%index only%%'", sql)

    def test_sections_and_missing_facts_are_validated(self):
        value = {'points': [{**self.point, 'kind': 'steps'}],
                 'limitations': ['Applicability is unverified.'], 'clarifying_questions': ['Which jurisdiction?']}
        self.assertEqual(validate_explanation(value, [self.hit])['clarifying_questions'], ['Which jurisdiction?'])
        with self.assertRaises(ValueError):
            validate_explanation({**value, 'limitations': ['x' * 601]}, [self.hit])

    def test_abstention_and_timeout_have_distinct_statuses(self):
        from source_explanation import InsufficientSources
        class APITimeoutError(Exception):
            pass
        for error, expected in [(InsufficientSources(), 'insufficient_sources'), (APITimeoutError(), 'timeout')]:
            explainer = Mock()
            explainer.explain.side_effect = error
            self.assertEqual(add_explanation({'question': 'x', 'hits': [self.hit]}, explainer)
                             ['explanation_status'], expected)