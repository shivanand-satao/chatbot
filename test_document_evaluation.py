import unittest

from evaluate_document_retrieval import assess_case, empty_case
from test_document_context import evidence


class EvaluationTests(unittest.TestCase):
    def test_valid_case(self):
        case = assess_case(evidence(), 1, 'Uploaded edition', '1.', 1)
        self.assertTrue(case['passed'])

    def test_wrong_document_version_section_and_seed_fail(self):
        for args, check in [((2, 'Uploaded edition', '1.', 1), 'document_filter'),
                            ((1, 'Other edition', '1.', 1), 'version_filter'),
                            ((1, 'Uploaded edition', '2.', 1), 'section_filter'),
                            ((1, 'Uploaded edition', '1.', 9), 'self_retrieval')]:
            case = assess_case(evidence(), *args)
            self.assertFalse(case['passed'])
            self.assertFalse(case['checks'][check])

    def test_no_hits_does_not_vacuously_pass(self):
        result = evidence()
        result['hits'] = []
        self.assertFalse(assess_case(result, 1, 'Uploaded edition')['passed'])
        self.assertTrue(empty_case(result))

    def test_nonempty_does_not_pass_empty_check(self):
        self.assertFalse(empty_case(evidence()))


if __name__ == '__main__':
    unittest.main()