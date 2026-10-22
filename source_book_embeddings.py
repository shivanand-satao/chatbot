"""Bounded source-only CPU indexing; no generation, downloads, or migrations."""

import math

from psycopg.types.json import Jsonb

from metadata_embeddings import MODEL


STRATEGY = 'source-only-v1'


def source_text(row):
    return f'{row["context_text"]}\nOriginal passage:\n{row["original_text"]}'


def embed_pending(connection, embedder, limit=32):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Invalid write batch limit')
    rows = connection.execute('''SELECT id,revision,content_hash,context_text,original_text
        FROM chatbot.document_chunks WHERE status='pending' ORDER BY id LIMIT %s''',
        (limit,)).fetchall()
    eligible, review = [], []
    for row in rows:
        text = source_text(row)
        tokens = embedder.token_count(text)
        if not row['original_text'].strip() or not 1 <= tokens <= 500:
            review.append((row['id'], row['revision'], row['content_hash']))
        else:
            eligible.append((row, text, tokens))
    vectors = embedder.encode([text for _, text, _ in eligible]) if eligible else []
    if len(vectors) != len(eligible):
        raise ValueError('Embedding count mismatch')
    params = []
    for (row, text, tokens), vector in zip(eligible, vectors):
        if (len(vector) != 384 or not all(math.isfinite(v) for v in vector)
                or abs(math.sqrt(sum(v*v for v in vector)) - 1) > 1e-5):
            raise ValueError('Invalid embedding')
        metadata = {'embedding_strategy': STRATEGY, 'enrichment': 'not_generated',
                    'generator_model': None, 'label': 'Original passage and document context only'}
        params.append((Jsonb(metadata), text, '[' + ','.join(map(str, vector)) + ']', MODEL,
                       embedder.revision, tokens, row['id'], row['revision'], row['content_hash']))
    # Revision/hash/status guards prevent stale writes; one bounded atomic batch.
    with connection.transaction():
        with connection.cursor() as cursor:
            if review:
                cursor.executemany('''UPDATE chatbot.document_chunks SET status='review'
                    WHERE id=%s AND revision=%s AND content_hash=%s AND status='pending' ''', review)
            if params:
                cursor.executemany('''UPDATE chatbot.document_chunks SET semantic_metadata=%s,
                    embedding_text=%s,embedding=%s::public.vector,embedding_model=%s,
                    model_revision=%s,embedded_content_hash=content_hash,token_count=%s,
                    embedded_at=now(),status='ready'
                    WHERE id=%s AND revision=%s AND content_hash=%s AND status='pending' ''', params)
    return {'selected': len(rows), 'encoded': len(params), 'review_candidates': len(review)}