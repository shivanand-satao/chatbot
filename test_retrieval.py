"""Offline retrieval tests: no database, model loads or external API calls."""

import unittest
from unittest.mock import Mock

from retrieval import Retriever, rank_candidates, validate_question, vector_literal


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.conn = Mock()
        self.conn.execute.return_value.fetchone.return_value = {'transaction_read_only': 'on'}
        self.embedder = Mock(revision='test-revision')
        self.embedder.encode.return_value = [[1.0] + [0.0] * 383]
        self.retriever = Retriever(self.conn, self.embedder)

    def test_read_only_guard(self):
        self.conn.execute.return_value.fetchone.return_value = {'transaction_read_only': 'off'}
        with self.assertRaises(RuntimeError):
            Retriever(self.conn, self.embedder)

    def test_invalid_questions_and_limits(self):
        for question in ('', ' ', None, 'x' * 2001):
            with self.assertRaises(ValueError):
                validate_question(question, 5)
        for limit in (0, 11, True, 1.5):
            with self.assertRaises(ValueError):
                validate_question('test', limit)

    def test_vectors_reject_invalid_dimensions_norm_and_nan(self):
        for vector in ([1], [0] * 384, [float('nan')] * 384, [float('inf')] * 384):
            with self.assertRaises(ValueError):
                vector_literal(vector)

    def test_ranking_groups_dataset_chunks(self):
        rows = [{'id': 1, 'table': 'a', 'text': 'auto theft', 'similarity': .9},
                {'id': 2, 'table': 'a', 'text': 'auto theft year', 'similarity': .89},
                {'id': 3, 'table': 'b', 'text': 'theft', 'similarity': .8}]
        hits = rank_candidates(rows, 'auto theft', 'text', 5, lambda row: row['table'])
        self.assertEqual([row['table'] for row in hits], ['a', 'b'])
        self.assertNotIn('rank_score', rows[0])

    def test_dataset_query_uses_parameters_and_query_prefix(self):
        self.conn.execute.return_value.fetchall.return_value = []
        question = "theft'; DROP TABLE users; --"
        result = self.retriever.datasets(question)
        sql, parameters = self.conn.execute.call_args.args
        self.assertNotIn(question, sql)
        self.assertIn('test-revision', parameters)
        self.embedder.encode.assert_called_once_with([question], query=True)
        self.assertEqual(result['status'], 'no_compatible_candidates')

    def test_pending_document_does_not_embed_or_search(self):
        self.conn.execute.return_value.fetchall.return_value = [
            {'id': 5, 'chunks': 100, 'compatible_ready': 0}]
        result = self.retriever.documents('Constitution safeguards', document_id=5)
        self.assertEqual(result['hits'], [])
        self.assertEqual(result['status'], 'no_ready_evidence')
        self.assertTrue(result['partial_coverage'])
        self.embedder.encode.assert_not_called()

    def test_document_filters_and_source_provenance(self):
        self.conn.execute.return_value.fetchall.side_effect = [
            [{'id': 1, 'chunks': 100, 'compatible_ready': 1}],
            [{'id': 7, 'document_id': 1, 'revision': 2, 'original_text': 'Procedure', 'similarity': .8}]]
        result = self.retriever.documents('Procedure', document_id=1, version_label='edition', section='1.')
        sql, parameters = next(call.args for call in reversed(self.conn.execute.call_args_list)
                               if 'ORDER BY c.embedding <=>' in call.args[0])
        self.assertIn('chatbot.ready_document_chunks', sql)
        self.assertIn('edition', parameters)
        self.assertIn('1.', parameters)
        self.assertIn('ORDER BY c.embedding <=> %s::public.vector LIMIT 100', sql)
        self.assertNotIn('ORDER BY c.id', sql)
        self.assertEqual(result['retrieval']['candidate_count'], 1)
        self.assertTrue(result['retrieval']['approximate_recall'])
        self.assertEqual(result['hits'][0]['source_id'], 'document:1:chunk:7:revision:2')

    def test_large_collection_uses_bounded_candidates(self):
        self.conn.execute.return_value.fetchall.side_effect = [
            [{'id': 1, 'chunks': 10348, 'compatible_ready': 10348}],
            [{'id': 7, 'document_id': 1, 'revision': 1, 'original_text': 'Evidence', 'similarity': .8}]]
        result = self.retriever.documents('Evidence')
        self.assertEqual(len(result['hits']), 1)
        self.assertFalse(result['partial_coverage'])
        statements = [call.args[0] for call in self.conn.execute.call_args_list]
        self.assertIn("SET LOCAL hnsw.iterative_scan='strict_order'", statements)

    def test_document_candidate_overflow_fails_closed(self):
        self.conn.execute.return_value.fetchall.side_effect = [
            [{'id': 1, 'chunks': 10348, 'compatible_ready': 10348}], [{}]*101]
        with self.assertRaises(RuntimeError):
            self.retriever.documents('Evidence')

    def test_filters_reject_invalid_ids_and_empty_sections(self):
        for document_id in (0, -1, True, '1'):
            with self.assertRaises(ValueError):
                self.retriever.documents('test', document_id=document_id)
        with self.assertRaises(ValueError):
            self.retriever.documents('test', section=' ')

    def test_catalog_over_scan_budget_fails_closed(self):
        self.conn.execute.return_value.fetchall.return_value = [{}] * 2001
        with self.assertRaises(RuntimeError):
            self.retriever.datasets('theft')


if __name__ == '__main__':
    unittest.main()