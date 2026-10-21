import unittest
from unittest.mock import Mock

from audit_readiness import OPTIONS, assess, collect, dependencies


def baseline():
    return {
        'metadata': {'datasets': 1, 'invalid_vectors': 0},
        'missing_source_tables': [], 'column_drift_count': 0,
        'metadata_models': [{'embedding_model': 'e5', 'model_revision': 'revision'}],
        'document_models': [{'embedding_model': 'e5', 'model_revision': 'revision'}],
        'documents': [{'ready': 2, 'chunks': 2}], 'invalid_ready_chunks': 0,
        'role': dict.fromkeys(('rolsuper', 'rolbypassrls', 'rolcreaterole', 'rolcreatedb'), False),
        'privileges': [], 'application_grants': [],
    }


class ReadinessTests(unittest.TestCase):
    def test_baseline_has_no_coverage_or_privilege_blockers(self):
        self.assertEqual(assess(baseline()), [])

    def test_partial_documents_block_full_coverage(self):
        report = baseline()
        report['documents'][0]['ready'] = 1
        self.assertTrue(any('coverage' in value for value in assess(report)))

    def test_schema_drift_is_detected(self):
        report = baseline()
        report['column_drift_count'] = 1
        self.assertTrue(any('schema' in value for value in assess(report)))

    def test_elevated_role_and_auth_access_are_reported(self):
        report = baseline()
        report['role']['rolbypassrls'] = True
        report['privileges'] = [{'schema': 'public', 'table': 'users',
                                 'can_read': True, 'can_write': True, 'owns_table': False}]
        self.assertEqual(len(assess(report)), 3)

    def test_mismatched_model_is_reported(self):
        report = baseline()
        report['document_models'][0]['model_revision'] = 'other'
        self.assertTrue(any('models/revisions differ' in value for value in assess(report)))

    def test_invalid_ready_vectors_are_reported(self):
        report = baseline()
        report['invalid_ready_chunks'] = 1
        self.assertTrue(any('consistency' in value for value in assess(report)))

    def test_public_application_grants_are_reported(self):
        report = baseline()
        report['application_grants'] = [{'schema_access': True, 'table_access': False}]
        self.assertTrue(any('Application role' in value for value in assess(report)))

    def test_collection_rejects_writable_transactions(self):
        conn = Mock()
        conn.execute.return_value.fetchone.return_value = {'transaction_read_only': 'off'}
        with self.assertRaises(RuntimeError):
            collect(conn)
        conn.execute.assert_called_once_with('SHOW transaction_read_only')
        self.assertIn('default_transaction_read_only=on', OPTIONS)

    def test_dependency_inventory_does_not_import_model(self):
        self.assertIn('fastapi', dependencies())


if __name__ == '__main__':
    unittest.main()