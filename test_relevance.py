import json
import unittest

from evaluate_relevance import relevance_metrics
from metadata_embeddings import ROOT
from verify_indexed_retrieval import candidate_recall


class RelevanceTests(unittest.TestCase):
    def test_ranks(self):
        metrics = relevance_metrics([7, 8, 9], [9])
        self.assertTrue(metrics['hit_at_5'])
        self.assertAlmostEqual(metrics['reciprocal_rank_at_10'], 1/3)

    def test_miss(self):
        self.assertFalse(relevance_metrics([1], [2])['hit_at_10'])

    def test_top_ten_bound_is_enforced(self):
        metrics = relevance_metrics(list(range(1, 12)), [11])
        self.assertFalse(metrics['hit_at_10'])
        self.assertEqual(metrics['labeled_recall_at_10'], 0)

    def test_rank_six_is_not_top_five(self):
        metrics = relevance_metrics(list(range(1, 11)), [6])
        self.assertFalse(metrics['hit_at_5'])
        self.assertTrue(metrics['hit_at_10'])

    def test_invalid_labels(self):
        with self.assertRaises(ValueError):
            relevance_metrics([1], [])

    def test_recall_empty(self):
        self.assertEqual(candidate_recall([], []), 1)
        self.assertEqual(candidate_recall([{'id': 1}], []), 0)

    def test_recall_sets(self):
        self.assertEqual(candidate_recall([{'id': 1}, {'id': 1}], [{'id': 1}, {'id': 2}]), .5)

    def test_fixture_coverage_and_provenance(self):
        fixture = json.loads((ROOT / 'document_relevance_cases.json').read_text(encoding='utf-8'))
        self.assertEqual({case['document_id'] for case in fixture['positive_cases']}, set(range(1, 7)))
        names = [case['name'] for case in fixture['positive_cases']]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn('not independent human', fixture['label_origin'])
        for case in fixture['positive_cases']:
            self.assertTrue(case['expected'])
            for expected in case['expected']:
                self.assertRegex(expected['hash'], '^[a-f0-9]{32}$')


if __name__ == '__main__':
    unittest.main()