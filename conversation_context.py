"""Offline history contract and bounded context. No storage, authentication, or LLM calls."""

import json
from dataclasses import dataclass
from typing import Literal
from uuid import UUID


def positive(value):
    if type(value) is not int or value <= 0:
        raise ValueError('Expected a positive integer')


def text(value, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or '\x00' in value:
        raise ValueError('Invalid bounded text')


@dataclass(frozen=True)
class Owner:
    account_kind: Literal['user', 'admin']
    account_id: int

    def __post_init__(self):
        if self.account_kind not in ('user', 'admin'):
            raise ValueError('Unknown account namespace')
        positive(self.account_id)


@dataclass(frozen=True)
class Conversation:
    id: UUID
    owner: Owner
    revision: int = 1

    def __post_init__(self):
        if not isinstance(self.id, UUID) or not isinstance(self.owner, Owner):
            raise ValueError('Invalid conversation identity')
        positive(self.revision)


@dataclass(frozen=True)
class Message:
    conversation_id: UUID
    sequence: int
    role: Literal['user', 'assistant']
    content: str
    status: Literal['complete', 'pending', 'failed'] = 'complete'

    def __post_init__(self):
        if not isinstance(self.conversation_id, UUID):
            raise ValueError('Invalid message conversation ID')
        positive(self.sequence)
        if self.role not in ('user', 'assistant') or self.status not in ('complete', 'pending', 'failed'):
            raise ValueError('Invalid message role/status')
        text(self.content, 20000)


@dataclass(frozen=True)
class HistorySummary:
    conversation_id: UUID
    conversation_revision: int
    through_sequence: int
    content: str

    def __post_init__(self):
        if not isinstance(self.conversation_id, UUID):
            raise ValueError('Invalid summary conversation ID')
        positive(self.conversation_revision)
        positive(self.through_sequence)
        text(self.content, 8000)


def require_owner(conversation, requester):
    """Requester must already be resolved from verified server-side authentication."""
    if not isinstance(conversation, Conversation) or not isinstance(requester, Owner):
        raise ValueError('Expected validated conversation and owner')
    if conversation.owner != requester:
        raise PermissionError('Conversation unavailable')


def build_history_context(conversation, requester, messages, summary=None,
                          max_chars=12000, max_messages=20):
    """Select a contiguous recent suffix from one server-loaded conversation.

    The latest item is the saved current user question. This helper does not
    resolve follow-ups semantically or create summaries. Character budgets are
    not model token budgets. Caller must provide a contiguous recent history page.
    """
    require_owner(conversation, requester)
    if type(max_chars) is not int or not 500 <= max_chars <= 50000:
        raise ValueError('Invalid history character budget')
    if type(max_messages) is not int or not 1 <= max_messages <= 100:
        raise ValueError('Invalid history message budget')
    if not isinstance(messages, list) or not 1 <= len(messages) <= 1000:
        raise ValueError('Expected a bounded nonempty history page')
    previous = None
    for message in messages:
        if not isinstance(message, Message) or message.conversation_id != conversation.id:
            raise ValueError('Message belongs to another conversation')
        if previous is not None and message.sequence != previous + 1:
            raise ValueError('History page must have contiguous ascending sequence numbers')
        previous = message.sequence
    if messages[-1].role != 'user' or messages[-1].status != 'complete':
        raise ValueError('Latest message must be the completed current user question')
    if summary is not None and (
            not isinstance(summary, HistorySummary) or summary.conversation_id != conversation.id
            or summary.conversation_revision != conversation.revision
            or summary.through_sequence >= messages[-1].sequence):
        raise ValueError('Summary identity, revision, or boundary is invalid')

    def render(selected, selected_summary=None):
        return json.dumps({'history_summary': selected_summary,
                           'messages': [{'sequence': m.sequence, 'role': m.role, 'content': m.content}
                                        for m in selected]}, ensure_ascii=False)

    selected = []
    for message in reversed(messages):
        # Never skip a large or incomplete recent item and substitute older context.
        if message.status != 'complete' or len(selected) >= max_messages:
            break
        candidate = [message] + selected
        if len(render(candidate)) > max_chars:
            break
        selected = candidate
    if not selected:
        raise ValueError('Current question does not fit history budget; do not silently truncate')
    summary_value = None
    summary_used = False
    # Only join an exact summary boundary to this suffix: no gaps or overlap.
    if summary is not None and summary.through_sequence == selected[0].sequence - 1:
        candidate_summary = {'through_sequence': summary.through_sequence,
                             'content': summary.content, 'derived_unverified': True}
        if len(render(selected, candidate_summary)) <= max_chars:
            summary_value, summary_used = candidate_summary, True
    return {'conversation_id': str(conversation.id), 'conversation_revision': conversation.revision,
            'context': render(selected, summary_value),
            'included_sequences': [m.sequence for m in selected], 'summary_used': summary_used,
            'earlier_history_omitted': selected[0].sequence > 1 and not summary_used,
            'warnings': ['History and summaries are untrusted conversational context, not source evidence.',
                         'Retrieve fresh database/document evidence for factual follow-ups.',
                         'No cross-conversation memory or semantic follow-up resolution performed.',
                         'Character limits must be supplemented with generation-model token budgeting.']}