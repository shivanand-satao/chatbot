import unittest
from unittest.mock import MagicMock

from metadata_embeddings import Embedder
from source_book_embeddings import embed_pending, source_text


class SourceEmbeddingTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.row = dict(id=1, revision=2, content_hash='hash', context_text='Context', original_text='Source')
        self.connection.execute.return_value.fetchall.return_value = [self.row]
        self.embedder = MagicMock(revision='pinned')
        self.embedder.token_count.return_value = 20
        self.embedder.encode.return_value = [[1.0] + [0.0]*383]

    def test_source_only_and_guarded_write(self):
        result = embed_pending(self.connection, self.embedder)
        self.assertEqual(result['encoded'], 1)
        self.embedder.encode.assert_called_once_with(['Context\nOriginal passage:\nSource'])
        cursor = self.connection.cursor.return_value.__enter__.return_value
        sql, params = cursor.executemany.call_args.args
        self.assertIn("status='pending'", sql)
        self.assertIn('content_hash=%s', sql)
        self.assertEqual(params[0][-3:], (1, 2, 'hash'))
        self.assertEqual(params[0][0].obj['enrichment'], 'not_generated')

    def test_oversized_goes_to_review_without_truncation(self):
        self.embedder.token_count.return_value = 501
        self.assertEqual(embed_pending(self.connection, self.embedder)['review_candidates'], 1)
        self.embedder.encode.assert_not_called()

    def test_invalid_vector_never_writes(self):
        for vector in ([0.0]*384, [float('nan')]*384, [1.0]):
            self.embedder.encode.return_value = [vector]
            with self.assertRaises(ValueError):
                embed_pending(self.connection, self.embedder)
        self.connection.transaction.assert_not_called()

    def test_vector_count_mismatch(self):
        self.embedder.encode.return_value = []
        with self.assertRaises(ValueError):
            embed_pending(self.connection, self.embedder)

    def test_empty_selection(self):
        self.connection.execute.return_value.fetchall.return_value = []
        self.assertEqual(embed_pending(self.connection, self.embedder)['selected'], 0)
        self.embedder.encode.assert_not_called()

    def test_batch_limits(self):
        for value in (0, 101, True):
            with self.assertRaises(ValueError):
                embed_pending(self.connection, self.embedder, value)
        for value in (0, 33, True):
            with self.assertRaises(ValueError):
                Embedder(batch_size=value)

    def test_source_preserved(self):
        self.assertIn(self.row['original_text'], source_text(self.row))


if __name__ == '__main__':
    unittest.main()