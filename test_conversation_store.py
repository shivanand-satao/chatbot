"""Offline SQL contract tests, not PostgreSQL integration or concurrency tests."""

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row

from conversation_context import Owner
from conversation_store import ConversationConflict, ConversationStore, validate_sources
from query_plan import compile_plan


class ConversationStoreTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.connection.autocommit = True
        self.connection.row_factory = dict_row
        self.connection.info.transaction_status = TransactionStatus.IDLE
        self.store = ConversationStore(self.connection, Owner('user', 7))
        self.cid, self.rid = uuid4(), uuid4()
        self.conversation = {'id': self.cid, 'revision': 1, 'next_sequence': 1}
        self.results = []
        self.calls = []

        def execute(query, params=None):
            sql = ' '.join(query.split())
            self.calls.append((sql, params))
            cursor = MagicMock()
            if not sql.startswith('SET LOCAL'):
                if not self.results:
                    raise AssertionError('Unexpected SQL: ' + sql)
                value = self.results.pop(0)
                cursor.fetchone.return_value = value
                cursor.fetchall.return_value = value
            return cursor

        self.connection.execute.side_effect = execute

    def test_constructor_rejects_unvalidated_owner_and_wrong_row_factory(self):
        with self.assertRaises(ValueError):
            ConversationStore(self.connection, {'account_id': 7})
        self.connection.row_factory = None
        with self.assertRaises(ValueError):
            ConversationStore(self.connection, Owner('user', 7))

    def test_history_tables_are_never_analytical_tables(self):
        for table in ('conversations', 'conversation_requests', 'messages'):
            with self.assertRaises(ValueError):
                compile_plan({'table': table, 'columns': ['id']}, {table: {'id': 'integer'}})

    def test_nested_or_non_autocommit_connection_rejected(self):
        self.connection.autocommit = False
        with self.assertRaises(RuntimeError):
            self.store.create()
        self.connection.autocommit = True
        self.connection.info.transaction_status = TransactionStatus.INTRANS
        with self.assertRaises(RuntimeError):
            self.store.create()
        self.connection.execute.assert_not_called()

    def test_create_binds_identity_and_untrusted_title(self):
        title = "test'); DROP TABLE messages; --"
        self.results = [self.conversation]
        self.assertEqual(self.store.create(title), self.conversation)
        sql, params = self.calls[-1]
        self.assertNotIn(title, sql)
        self.assertEqual(params[1:], ('user', 7, title))

    def test_list_uses_owner_and_stable_cursor(self):
        cursor = (datetime.now(timezone.utc), uuid4())
        self.results = [[]]
        self.assertEqual(self.store.list_conversations(5, cursor), [])
        self.assertEqual(self.calls[-1][1], ('user', 7, *cursor, 5))
        self.assertIn('ORDER BY created_at DESC,id DESC', self.calls[-1][0])

    def test_invalid_pagination_rejected_before_sql(self):
        for value in (0, 101, True, '2'):
            with self.assertRaises(ValueError):
                self.store.list_conversations(value)
        with self.assertRaises(ValueError):
            self.store.list_conversations(before=(datetime.now(), uuid4()))
        with self.assertRaises(ValueError):
            self.store.messages(self.cid, before_sequence=True)
        self.connection.execute.assert_not_called()

    def test_last_active_scopes_namespace(self):
        self.store = ConversationStore(self.connection, Owner('admin', 7))
        self.results = [None]
        self.assertIsNone(self.store.last_active())
        self.assertEqual(self.calls[-1][1], ('admin', 7))
        self.assertIn('updated_at DESC', self.calls[-1][0])

    def test_missing_or_foreign_conversation_stops_message_read(self):
        self.results = [None]
        with self.assertRaises(PermissionError):
            self.store.messages(self.cid)
        self.assertEqual(self.calls[-1][1], (self.cid, 'user', 7))
        self.assertNotIn('chatbot.messages', self.calls[-1][0])

    def test_history_returns_ascending_page_without_status_filter(self):
        self.results = [self.conversation, [{'sequence': 3}, {'sequence': 2}]]
        self.assertEqual(self.store.messages(self.cid, 2, 4), [{'sequence': 2}, {'sequence': 3}])
        self.assertEqual(self.calls[-1][1], (self.cid, 4, 4, 2))
        self.assertNotIn('status=', self.calls[-1][0])

    def test_delete_locks_owned_row_before_delete(self):
        self.results = [self.conversation, None]
        self.store.delete(self.cid)
        self.assertIn('FOR UPDATE', self.calls[-2][0])
        self.assertEqual(self.calls[-1][1], (self.cid,))

    def test_foreign_delete_does_not_issue_delete(self):
        self.results = [None]
        with self.assertRaises(PermissionError):
            self.store.delete(self.cid)
        self.assertFalse(any(sql.startswith('DELETE') for sql, _ in self.calls))

    def test_begin_saves_user_request_and_advances_in_transaction(self):
        self.results = [self.conversation, None, None, None, None, None]
        result = self.store.begin_turn(self.cid, self.rid, 'question', 1)
        self.assertEqual(result, {'replayed': False, 'state': 'pending', 'sequence': 1})
        self.assertEqual(self.results, [])
        self.assertIn('FOR UPDATE', self.calls[2][0])
        self.connection.transaction.assert_called_once()
        self.assertIn('revision=revision+1', self.calls[-1][0])

    def test_begin_replay_is_checked_before_stale_revision(self):
        self.results = [self.conversation, {'content': 'question', 'sequence': 1, 'state': 'complete'}]
        self.assertTrue(self.store.begin_turn(self.cid, self.rid, 'question', 99)['replayed'])
        self.assertFalse(any(sql.startswith('INSERT') for sql, _ in self.calls))

    def test_begin_reused_identifier_changed_content_rejected(self):
        self.results = [self.conversation, {'content': 'different', 'sequence': 1, 'state': 'pending'}]
        with self.assertRaises(ConversationConflict):
            self.store.begin_turn(self.cid, self.rid, 'question', 1)

    def test_begin_stale_revision_rejected(self):
        self.results = [self.conversation, None]
        with self.assertRaises(ConversationConflict):
            self.store.begin_turn(self.cid, self.rid, 'question', 2)

    def test_begin_pending_turn_rejected(self):
        self.results = [self.conversation, None, {'exists': 1}]
        with self.assertRaises(ConversationConflict):
            self.store.begin_turn(self.cid, self.rid, 'question', 1)

    def test_finish_saves_answer_and_sources(self):
        sources = [{'source_id': 'doc:1', 'page_start': 2, 'page_end': 3}]
        self.results = [self.conversation, {'state': 'pending'}, None, None, None]
        self.assertFalse(self.store.finish_turn(self.cid, self.rid, 'answer', sources)['replayed'])
        insert = next(params for sql, params in self.calls if sql.startswith('INSERT'))
        self.assertEqual(insert[-1].obj, sources)

    def test_finish_replay_and_conflicting_overwrite(self):
        saved = {'content': 'answer', 'sources': [], 'status': 'complete', 'sequence': 2}
        self.results = [self.conversation, {'state': 'complete'}, saved]
        self.assertTrue(self.store.finish_turn(self.cid, self.rid, 'answer')['replayed'])
        self.results = [self.conversation, {'state': 'complete'}, saved]
        with self.assertRaises(ConversationConflict):
            self.store.finish_turn(self.cid, self.rid, 'changed')

    def test_finish_missing_request_rejected(self):
        self.results = [self.conversation, None]
        with self.assertRaises(ConversationConflict):
            self.store.finish_turn(self.cid, self.rid, 'answer')

    def test_failed_turn_is_recorded_without_sources(self):
        self.results = [self.conversation, {'state': 'pending'}, None, None, None]
        self.assertEqual(self.store.finish_turn(self.cid, self.rid, 'Generation failed', failed=True)['state'], 'failed')
        with self.assertRaises(ValueError):
            self.store.finish_turn(self.cid, self.rid, 'failure', [{'source_id': 'x'}], failed=True)

    def test_source_and_content_validation_before_sql(self):
        invalid = [[{'source_id': 'x', 'sql': 'secret'}], [{'source_id': 'x', 'page_start': 2}],
                   [{'source_id': 'x', 'page_start': 3, 'page_end': 2}],
                   [{'source_id': 'x'}, {'source_id': 'x'}], [{'source_id': 'x' * 501}], {}]
        for sources in invalid:
            with self.assertRaises(ValueError):
                validate_sources(sources)
        with self.assertRaises(ValueError):
            self.store.begin_turn(self.cid, self.rid, ' ', 1)
        with self.assertRaises(ValueError):
            self.store.finish_turn(self.cid, 'bad-id', 'answer')
        self.connection.execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()