"""Bounded evidence retrieval only: no Groq, generated SQL, or answer generation."""

import math
import re
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from metadata_embeddings import MODEL

STOPWORDS = {'the', 'a', 'an', 'of', 'in', 'for', 'and', 'to', 'what', 'which', 'are', 'is'}


@contextmanager
def read_connection(db_url):
    with psycopg.connect(db_url, sslmode='require', connect_timeout=10,
                         row_factory=dict_row) as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        conn.execute("SET LOCAL statement_timeout='15000ms'")
        conn.execute("SET LOCAL lock_timeout='3000ms'")
        try:
            yield conn
        finally:
            conn.rollback()


def validate_question(question, limit):
    if not isinstance(question, str) or not question.strip() or len(question) > 2000:
        raise ValueError('Question must contain 1–2000 characters')
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError('Result limit must be an integer from 1 to 10')
    return question.strip()


def vector_literal(vector):
    if len(vector) != 384 or any(not math.isfinite(x) for x in vector):
        raise ValueError('Expected 384 finite vector values')
    if abs(sum(x * x for x in vector) - 1) > 0.0001:
        raise ValueError('Query vector must be normalized')
    return '[' + ','.join(str(float(x)) for x in vector) + ']'


def lexical_score(question, text):
    tokens = set(re.findall(r'\w+', question.casefold())) - STOPWORDS
    words = set(re.findall(r'\w+', text.casefold().replace('_', ' ')))
    return len(tokens & words) / max(len(tokens), 1)


def rank_candidates(rows, question, text_key, limit, group_key=None):
    """Reciprocal-rank fusion; scores are ranking signals, not answer confidence."""
    semantic = sorted(rows, key=lambda row: (-row['similarity'], row['id']))
    semantic_ranks = {row['id']: rank for rank, row in enumerate(semantic, 1)}
    lexical = sorted(((lexical_score(question, row[text_key]), row) for row in rows),
                     key=lambda pair: (-pair[0], pair[1]['id']))
    lexical_ranks = {row['id']: rank for rank, (score, row) in enumerate(lexical, 1) if score > 0}
    ranked = []
    for row in semantic:
        item = dict(row)
        item['lexical_overlap'] = lexical_score(question, row[text_key])
        item['rank_score'] = 1 / (60 + semantic_ranks[row['id']])
        if row['id'] in lexical_ranks:
            item['rank_score'] += 1 / (60 + lexical_ranks[row['id']])
        ranked.append(item)
    ranked.sort(key=lambda row: (-row['rank_score'], -row['similarity'], row['id']))
    result, seen = [], set()
    for row in ranked:
        group = group_key(row) if group_key else row['id']
        if group in seen:
            continue
        seen.add(group)
        result.append(row)
        if len(result) == limit:
            break
    return result


class Retriever:
    def __init__(self, connection, embedder):
        self.connection = connection
        self.embedder = embedder
        if connection.execute('SHOW transaction_read_only').fetchone()['transaction_read_only'] != 'on':
            raise RuntimeError('Retriever requires a read-only transaction')

    def query_vector(self, question):
        return vector_literal(self.embedder.encode([question], query=True)[0])

    def datasets(self, question, limit=5):
        question = validate_question(question, limit)
        vector = self.query_vector(question)
        rows = self.connection.execute('''SELECT id,dataset_schema,dataset_table,metadata_type,
            original_text,metadata_details,updated_at::text,
            1-(embedding <=> %s::public.vector) AS similarity
            FROM chatbot.dataset_metadata_embeddings
            WHERE embedding_model=%s AND model_revision=%s AND dataset_schema='public'
            ORDER BY id LIMIT 2001''', (vector, MODEL, self.embedder.revision)).fetchall()
        if len(rows) > 2000:
            raise RuntimeError('Metadata catalog exceeds baseline scan budget; implement indexed candidate retrieval')
        hits = rank_candidates(rows, question, 'original_text', limit,
                               lambda row: (row['dataset_schema'], row['dataset_table']))
        return {'kind': 'datasets', 'status': 'candidates' if hits else 'no_compatible_candidates',
                'hits': hits, 'model': MODEL, 'revision': self.embedder.revision,
                'limitations': ['Candidates do not establish answerability or numerical results.',
                                'Metadata descriptions may be AI-inferred; verify schema, coverage and units.',
                                'Similarity is not calibrated confidence. No SQL was generated or executed.']}

    def documents(self, question, limit=5, document_id=None, version_label=None, section=None):
        question = validate_question(question, limit)
        if document_id is not None and (type(document_id) is not int or document_id <= 0):
            raise ValueError('Document ID must be a positive integer')
        for value in (version_label, section):
            if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 300):
                raise ValueError('Document filters must be nonempty strings of at most 300 characters')
        coverage = self.connection.execute('''SELECT d.id,d.title,d.version_label,d.page_count,
            count(c.id) AS chunks,count(c.id) FILTER (WHERE c.status='ready'
            AND c.content_hash=c.embedded_content_hash AND c.embedding_model=%s
            AND c.model_revision=%s) AS compatible_ready
            FROM chatbot.documents d LEFT JOIN chatbot.document_chunks c ON c.document_id=d.id
            WHERE (%s::bigint IS NULL OR d.id=%s)
            AND (%s::text IS NULL OR d.version_label=%s)
            GROUP BY d.id ORDER BY d.id''',
            (MODEL, self.embedder.revision, document_id, document_id, version_label, version_label)).fetchall()
        hits = []
        if any(row['compatible_ready'] for row in coverage):
            vector = self.query_vector(question)
            # pgvector >=0.8: filtered ANN can continue within bounded scan effort.
            self.connection.execute("SET LOCAL hnsw.iterative_scan='strict_order'")
            self.connection.execute('SET LOCAL hnsw.ef_search=400')
            self.connection.execute('SET LOCAL hnsw.max_scan_tuples=20000')
            # Hash-equality selectivity can make the planner prefer a full sort.
            # Scope the planner preference to this candidate query only.
            self.connection.execute('SET LOCAL enable_seqscan=off')
            self.connection.execute('SET LOCAL enable_sort=off')
            rows = self.connection.execute('''SELECT c.id,c.document_id,d.title,d.filename,
                d.version_label,c.page_start,c.page_end,c.section_reference,c.original_text,
                c.context_text,c.revision,c.content_hash,
                1-(c.embedding <=> %s::public.vector) AS similarity
                FROM chatbot.ready_document_chunks c JOIN chatbot.documents d ON d.id=c.document_id
                WHERE c.embedding_model=%s AND c.model_revision=%s
                AND (%s::bigint IS NULL OR c.document_id=%s)
                AND (%s::text IS NULL OR d.version_label=%s)
                AND (%s::text IS NULL OR c.section_reference=%s)
                ORDER BY c.embedding <=> %s::public.vector LIMIT 100''',
                (vector, MODEL, self.embedder.revision, document_id, document_id,
                 version_label, version_label, section, section, vector)).fetchall()
            self.connection.execute('SET LOCAL enable_seqscan=on')
            self.connection.execute('SET LOCAL enable_sort=on')
            if len(rows) > 100:
                raise RuntimeError('Document candidate budget exceeded')
            hits = rank_candidates(rows, question, 'original_text', limit)
            for hit in hits:
                hit['source_id'] = f"document:{hit['document_id']}:chunk:{hit['id']}:revision:{hit['revision']}"
        return {'kind': 'documents', 'status': 'candidate_passages' if hits else 'no_ready_evidence',
                'hits': hits, 'coverage': coverage,
                'partial_coverage': any(row['compatible_ready'] < row['chunks'] for row in coverage),
                'retrieval': {'method': 'bounded_vector_candidates', 'candidate_limit': 100,
                              'candidate_count': len(rows) if any(row['compatible_ready'] for row in coverage) else 0,
                              'approximate_recall': True},
                'limitations': ['Only model-compatible ready passages are searched; pending content is excluded.',
                                'Approximate vector candidates may miss relevant passages; lexical reranking is candidate-only.',
                                'Passages are candidates, not verified answers or current-law determinations.',
                                'Unscoped search may span editions; choose a document/version before legal synthesis.',
                                'No LLM answer generated. Legal research is not legal advice.']}