"""Bounded keyword source search, not vector retrieval or generated legal answers."""

import re
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


ROLE = 'chatbot_source_search'
STOPWORDS = frozenset('a an the is are was were be been being of on in to for from by with and or '
                     'what which who when where why how do does did can could would should '
                     'please tell me about explain find references document documents source '
                     'sources under available indian india law laws'.split())


def search_terms(question):
    if not isinstance(question, str) or not question.strip() or len(question) > 1500:
        raise ValueError('Question must contain 1–1500 characters')
    # Combining marks are retained in Indic words; no punctuation is passed as query syntax.
    words = re.findall(r'[^\W_]+(?:[\u0900-\u097f][^\W_]*)*', question.casefold())
    terms = list(dict.fromkeys(word for word in words if word not in STOPWORDS and len(word) >= 2))
    return terms[:12]


@contextmanager
def search_connection(dsn):
    with psycopg.connect(dsn, sslmode='require', connect_timeout=8, row_factory=dict_row) as db:
        db.execute('SET TRANSACTION READ ONLY')
        db.execute("SET LOCAL statement_timeout='8000ms'")
        db.execute("SET LOCAL lock_timeout='2000ms'")
        role = db.execute('''SELECT current_user AS name,rolsuper,rolbypassrls,rolcreaterole,
            rolcreatedb,rolreplication FROM pg_roles WHERE rolname=current_user''').fetchone()
        if role['name'] != ROLE or any(role[k] for k in role if k != 'name'):
            raise RuntimeError('Dedicated source-search identity required')
        if db.execute('SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=current_user)').fetchone():
            raise RuntimeError('Source-search role memberships not allowed')
        yield db
        db.rollback()


def document_list(db):
    return db.execute('''SELECT d.id,d.title,d.version_label,d.page_count,
        count(c.id)::int AS searchable_passages FROM chatbot.documents d
        LEFT JOIN chatbot.document_chunks c ON c.document_id=d.id
          AND c.status='ready' AND c.content_hash=c.embedded_content_hash
        GROUP BY d.id,d.title,d.version_label,d.page_count ORDER BY d.id LIMIT 100''').fetchall()


def search(db, question, document_id=None):
    terms = search_terms(question)
    if document_id is not None and (type(document_id) is not int or not 1 <= document_id <= 9223372036854775807):
        raise ValueError('Invalid document selection')
    hits = []
    if terms:
        # Each term is independently converted as plain text, then OR-combined by PostgreSQL.
        hits = db.execute('''WITH query AS (
            SELECT string_agg(plainto_tsquery('simple',term)::text,' | ')::tsquery AS q
            FROM unnest(%s::text[]) AS term)
            SELECT c.id,c.document_id,d.title,d.version_label,c.page_start,c.page_end,
                c.section_reference,c.revision,left(c.original_text,3500) AS passage,
                length(c.original_text)>3500 AS excerpt_truncated
            FROM chatbot.document_chunks c JOIN chatbot.documents d ON d.id=c.document_id
            CROSS JOIN query
            WHERE c.status='ready' AND c.content_hash=c.embedded_content_hash
              AND (%s::bigint IS NULL OR c.document_id=%s)
              AND to_tsvector('simple',c.original_text) @@ query.q
            ORDER BY ts_rank_cd(to_tsvector('simple',c.original_text),query.q) DESC,c.id
            LIMIT 5''', (terms, document_id, document_id)).fetchall()
    for hit in hits:
        hit['source_id'] = f"document:{hit['document_id']}:chunk:{hit['id']}:revision:{hit['revision']}"
    return {'question': question.strip(), 'status': 'candidate_passages' if hits else 'no_matches',
            'method': 'keyword_search', 'terms': terms, 'hits': hits,
            'answer_generated': False, 'generation_allowed': False,
            'notice': 'Keyword matches are source candidates, not verified answers. '
                      'Editions may include historical texts or introduced bills, not current law. '
                      'PDF page numbers are shown. Legal research is not legal advice. '
                      'Questions and results are not saved.'}