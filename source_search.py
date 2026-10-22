"""Bounded keyword source search, not vector retrieval or generated legal answers."""

import re
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row


ROLE = 'chatbot_source_search'
STOPWORDS = frozenset('a an the is are was were be been being of on in to for from by with and or '
                     'what which who when where why how do does did can could would should '
                     'please tell me about explain find references document documents source '
                     'sources under available indian india law laws now then also just '
                     'i my we our you your he she it this that any all if without have has '
                     'had am will shall so there here get give want know'.split())

# Small, explicit spelling/inflection map, not model-written query syntax.
TERM_ALIASES = {'arreest': 'arrest', 'arresst': 'arrest', 'arrested': 'arrest',
                'arrests': 'arrest', 'arresting': 'arrest', 'wiihtou': 'without',
                'wihtout': 'without', 'withou': 'without', 'notices': 'notice',
                'safeguards': 'safeguard', 'rights': 'right'}


def retrieval_terms(question):
    # Normalize before stopword removal, so misspelled filler does not become evidence.
    normalized = re.sub(r'[^\W_]+', lambda match: TERM_ALIASES.get(match[0].casefold(),
                        match[0].casefold()), question)
    terms = search_terms(normalized)
    if 'arrest' in terms:
        # Arrest is the core concept; police/notice alone are insufficient matches.
        return terms, ['arrest']
    return terms, []


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
    search_terms(question)  # Validate before normalization.
    terms, required = retrieval_terms(question)
    if document_id is not None and (type(document_id) is not int or not 1 <= document_id <= 9223372036854775807):
        raise ValueError('Invalid document selection')
    hits = []
    if terms:
        # Terms are server-tokenized, bound, and prefix-matched (arrest/arrested).
        # Coverage outranks single-word density; diversity never forces irrelevant documents.
        hits = db.execute('''WITH query AS (
            SELECT to_tsquery('simple',string_agg(quote_literal(term) || ':*',' | ')) AS q
            FROM unnest(%s::text[]) AS term), required AS (
            SELECT to_tsquery('simple',string_agg(quote_literal(term) || ':*',' & ')) AS q
            FROM unnest(%s::text[]) AS term)
            , ranked AS (SELECT c.id,c.document_id,d.title,d.version_label,c.page_start,c.page_end,
                c.section_reference,c.revision,left(c.original_text,6000) AS passage,
                length(c.original_text)>6000 AS excerpt_truncated,
                (SELECT count(*) FROM unnest(%s::text[]) t WHERE
                    to_tsvector('simple',c.original_text) @@
                    to_tsquery('simple',quote_literal(t) || ':*')) AS coverage,
                ts_rank_cd(to_tsvector('simple',c.original_text),query.q) AS score,
                row_number() OVER (PARTITION BY c.document_id ORDER BY
                    (SELECT count(*) FROM unnest(%s::text[]) t WHERE
                        to_tsvector('simple',c.original_text) @@
                        to_tsquery('simple',quote_literal(t) || ':*')) DESC,
                    ts_rank_cd(to_tsvector('simple',c.original_text),query.q) DESC,c.id) AS document_rank
            FROM chatbot.document_chunks c JOIN chatbot.documents d ON d.id=c.document_id
            CROSS JOIN query CROSS JOIN required
            WHERE c.status='ready' AND c.content_hash=c.embedded_content_hash
              AND (%s::bigint IS NULL OR c.document_id=%s)
              AND to_tsvector('simple',c.original_text) @@ query.q
              AND (required.q IS NULL OR to_tsvector('simple',c.original_text) @@ required.q)
              AND d.version_label NOT ILIKE '%%index only%%'
            ) SELECT id,document_id,title,version_label,page_start,page_end,
                section_reference,revision,passage,excerpt_truncated FROM ranked
            WHERE document_rank <= 3 ORDER BY coverage DESC,score DESC,document_rank,id
            LIMIT 10''', (terms, required, terms, terms, document_id, document_id)).fetchall()
    for hit in hits:
        hit['source_id'] = f"document:{hit['document_id']}:chunk:{hit['id']}:revision:{hit['revision']}"
    return {'question': question.strip(), 'status': 'candidate_passages' if hits else 'no_matches',
            'method': 'keyword_search', 'terms': terms, 'hits': hits,
            'search_scope': 'all_documents' if document_id is None else 'selected_document',
            'answer_generated': False, 'generation_allowed': False,
            'notice': 'Analysis is based on uploaded editions, which may be historical texts or bills. '
                      'Current legal validity is not verified. Questions and results are not saved.'}