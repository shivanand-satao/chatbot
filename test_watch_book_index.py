import unittest
from unittest.mock import patch

from watch_book_index import main, progress_line


class ProgressTests(unittest.TestCase):
    def test_counts(self):
        line = progress_line({'ready': 200, 'pending': 800})
        self.assertIn('Remaining (pending): 800', line)
        self.assertIn('20.00% ready', line)

    def test_empty(self):
        self.assertIn('0.00% ready', progress_line({}))

    def test_other_states_not_hidden(self):
        self.assertIn('Other statuses: 3', progress_line({'ready': 2, 'failed': 3}))

    @patch('watch_book_index.read_counts', side_effect=RuntimeError('secret connection'))
    @patch('builtins.print')
    def test_error_is_sanitized(self, output, _read):
        with patch('sys.argv', ['watch_book_index.py', '--once']):
            self.assertEqual(main(), 1)
        self.assertNotIn('secret connection', str(output.call_args_list))


if __name__ == '__main__':
    unittest.main()