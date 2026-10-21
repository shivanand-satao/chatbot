"""Offline tests; do not contact PostgreSQL or load the embedding model."""

import unittest

from index_dataset_metadata import SYSTEM_TABLES, content_hash, discover
from metadata_embeddings import pack_text
from semantic_metadata import enriched_text, export_documents, validate_document
import json
import tempfile
from pathlib import Path


class MetadataTests(unittest.TestCase):
    def test_enriched_text_and_exports_preserve_provenance(self):
        columns = [{'column_name': 'Year', 'data_type': 'integer', 'is_nullable': 'YES',
                    'column_comment': None}]
        semantic = {'document': {'crime_category': 'Category', 'semantic_summary': 'Summary',
                                'granularity': 'Inferred', 'example_questions': ['Question?'],
                                'uncertainties': ['Units unknown'],
                                'column_descriptions': {'Year': 'Year dimension'}}}
        summary, text = enriched_text('Verified facts', columns, semantic)
        self.assertIn('AI-INFERRED', summary)
        self.assertIn('Verified facts', summary)
        self.assertIn('Year: integer;', text)
        self.assertIn('AI-inferred description: Year dimension', text)
        with tempfile.TemporaryDirectory() as directory:
            docs = [{'table_name': 'test', 'summary_text': summary, 'column_text': text}]
            export_documents(directory, docs)
            self.assertEqual(json.loads((Path(directory)/'crime_schema_summary.json').read_text()), docs)
            self.assertIn(text, (Path(directory)/'crime_schema_summary.md').read_text(encoding='utf-8'))

    def test_semantic_column_names_are_exact(self):
        value = {'crime_category': 'Test', 'semantic_summary': 'Summary', 'granularity': 'Unknown',
                 'column_descriptions': {'Year': 'Year value; coverage verified separately'},
                 'example_questions': ['What years are available?'], 'uncertainties': ['Units unverified']}
        self.assertEqual(validate_document(value, [{'column_name': 'Year'}]), value)
        with self.assertRaises(ValueError):
            validate_document(value, [{'column_name': 'year'}])
        value['column_descriptions']['Year'] = ''
        with self.assertRaises(ValueError):
            validate_document(value, [{'column_name': 'Year'}])

    def test_chunking_preserves_all_characters(self):
        text = 'first column: integer\n' + 'x' * 300 + '\nlast column: text\n'
        chunks = pack_text(text, 'Dataset: test\n', len, limit=80)
        self.assertEqual(''.join(c.removeprefix('Dataset: test\n') for c in chunks), text)
        self.assertTrue(all(len(c) <= 80 for c in chunks))

    def test_hash_is_stable_and_tracks_details(self):
        self.assertEqual(content_hash('text', {'b': 2, 'a': 1}), content_hash('text', {'a': 1, 'b': 2}))
        self.assertNotEqual(content_hash('text', {'rows': 1}), content_hash('text', {'rows': 2}))

    def test_long_unicode_metadata_is_not_lost(self):
        text = ('वर्ष और क्षेत्र विवरण ' * 100) + '\nArea_name: text\n'
        chunks = pack_text(text, 'Dataset: public.test\n', len, limit=120)
        self.assertEqual(''.join(c.removeprefix('Dataset: public.test\n') for c in chunks), text)
        self.assertTrue(all(len(c) <= 120 for c in chunks))

    def test_empty_metadata_produces_no_entries(self):
        self.assertEqual(pack_text('', 'context', len), [])

    def test_system_tables_excluded_and_unknown_tables_rejected(self):
        class FakeConnection:
            def __init__(self, rows):
                self.rows = rows

            def execute(self, query):
                return self

            def fetchall(self):
                return self.rows

        rows = [{'table_name': name, 'column_name': 'id'} for name in SYSTEM_TABLES]
        rows.append({'table_name': 'serious_fraud', 'column_name': 'Area_name'})
        self.assertEqual(list(discover(FakeConnection(rows))), ['serious_fraud'])
        rows.append({'table_name': 'private_information', 'column_name': 'email'})
        with self.assertRaises(ValueError):
            discover(FakeConnection(rows))


if __name__ == '__main__':
    unittest.main()