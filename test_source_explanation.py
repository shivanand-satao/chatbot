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
                       {'text': 'x' * 901}, {'extra': 'field'}):
            with self.assertRaises(ValueError):
                validate_explanation({'points': [{**self.point, **change}]}, [self.hit])
        with self.assertRaises(ValueError):
            validate_explanation({'points': []}, [self.hit])

    def test_provider_failure_keeps_sources(self):
        explainer = Mock()
        explainer.explain.side_effect = RuntimeError('private provider details')
        result = add_explanation({'question': 'protections', 'hits': [self.hit]}, explainer)
        self.assertEqual(result['explanation_status'], 'unavailable')
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
                         ['explanation_status'], 'unavailable')

    def test_no_matches_does_not_call_provider(self):
        explainer = Mock()
        self.assertEqual(add_explanation({'hits': []}, explainer)['explanation_status'], 'no_matches')
        explainer.explain.assert_not_called()

    def test_all_document_search_is_diversified_and_bounded(self):
        db = Mock()
        db.execute.return_value.fetchall.return_value = []
        result = search(db, 'freedom')
        sql, parameters = db.execute.call_args.args
        self.assertEqual(parameters, (['freedom'], None, None))
        self.assertIn('PARTITION BY c.document_id', sql)
        self.assertIn('document_rank <= 2', sql)
        self.assertIn('LIMIT 8', sql)
        self.assertEqual(result['search_scope'], 'all_documents')