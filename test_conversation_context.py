import json
import unittest
from uuid import uuid4

from conversation_context import (
    Conversation, HistorySummary, Message, Owner, build_history_context, require_owner,
)


class ConversationContextTests(unittest.TestCase):
    def setUp(self):
        self.owner = Owner('user', 1)
        self.conversation = Conversation(uuid4(), self.owner)

    def message(self, sequence, role='user', content='Question', status='complete'):
        return Message(self.conversation.id, sequence, role, content, status)

    def build(self, messages, **kwargs):
        return build_history_context(self.conversation, self.owner, messages, **kwargs)

    def test_current_question_and_prior_answer_remain_separate_roles(self):
        messages = [self.message(1), self.message(2, 'assistant', 'Previous evidence'),
                    self.message(3, content='What about Maharashtra?')]
        result = self.build(messages)
        payload = json.loads(result['context'])
        self.assertEqual(result['included_sequences'], [1, 2, 3])
        self.assertEqual(payload['messages'][1]['role'], 'assistant')
        self.assertFalse(result['earlier_history_omitted'])

    def test_other_user_and_same_id_admin_cannot_access(self):
        for owner in (Owner('user', 2), Owner('admin', 1)):
            with self.assertRaises(PermissionError):
                require_owner(self.conversation, owner)

    def test_other_conversation_message_rejected(self):
        with self.assertRaises(ValueError):
            self.build([Message(uuid4(), 1, 'user', 'Other conversation')])

    def test_duplicate_gap_and_reversed_sequences_rejected(self):
        for sequences in ([1, 1], [1, 3], [2, 1]):
            with self.assertRaises(ValueError):
                self.build([self.message(n) for n in sequences])

    def test_latest_question_never_silently_dropped(self):
        with self.assertRaises(ValueError):
            self.build([self.message(1, content='x' * 1000)], max_chars=500)

    def test_oversized_prior_turn_stops_suffix(self):
        result = self.build([self.message(1), self.message(2, 'assistant', 'x' * 1000),
                             self.message(3)], max_chars=500)
        self.assertEqual(result['included_sequences'], [3])
        self.assertTrue(result['earlier_history_omitted'])

    def test_message_limit(self):
        result = self.build([self.message(1), self.message(2)], max_messages=1)
        self.assertEqual(result['included_sequences'], [2])

    def test_summary_exact_boundary_and_unverified_label(self):
        summary = HistorySummary(self.conversation.id, 1, 2, 'Topic: theft; year unverified')
        result = self.build([self.message(3)], summary=summary)
        self.assertTrue(result['summary_used'])
        self.assertTrue(json.loads(result['context'])['history_summary']['derived_unverified'])

    def test_summary_gap_not_used(self):
        summary = HistorySummary(self.conversation.id, 1, 1, 'Earlier topic')
        self.assertFalse(self.build([self.message(3)], summary=summary)['summary_used'])

    def test_stale_foreign_and_future_summaries_rejected(self):
        for summary in (HistorySummary(uuid4(), 1, 1, 'Foreign'),
                        HistorySummary(self.conversation.id, 2, 1, 'Stale'),
                        HistorySummary(self.conversation.id, 1, 3, 'Future')):
            with self.assertRaises(ValueError):
                self.build([self.message(3)], summary=summary)

    def test_incomplete_prior_turn_stops_history(self):
        result = self.build([self.message(1), self.message(2, 'assistant', status='failed'),
                             self.message(3)])
        self.assertEqual(result['included_sequences'], [3])

    def test_latest_assistant_or_pending_message_rejected(self):
        for message in (self.message(1, 'assistant'), self.message(1, status='pending')):
            with self.assertRaises(ValueError):
                self.build([message])

    def test_empty_and_bad_budgets_rejected(self):
        with self.assertRaises(ValueError):
            self.build([])
        for kwargs in ({'max_chars': True}, {'max_chars': 499}, {'max_messages': 0}):
            with self.assertRaises(ValueError):
                self.build([self.message(1)], **kwargs)

    def test_invalid_record_fields_rejected(self):
        for kind, identifier in (('guest', 1), ('user', True), ('admin', 0)):
            with self.assertRaises(ValueError):
                Owner(kind, identifier)
        with self.assertRaises(ValueError):
            self.message(1, role='system')
        with self.assertRaises(ValueError):
            self.message(1, content='')


if __name__ == '__main__':
    unittest.main()