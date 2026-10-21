"""Validate ready vectors and test edit invalidation/history inside a rolled-back transaction."""
import json
import psycopg
from psycopg.rows import dict_row
from dotenv import dotenv_values
from metadata_embeddings import ROOT


def main():
    config=dotenv_values(ROOT/'.env',interpolate=False)
    with psycopg.connect(config['db_url'],sslmode='require',connect_timeout=10,row_factory=dict_row) as conn:
        ready=conn.execute('''SELECT *,public.vector_dims(embedding) AS dimensions,
            public.vector_norm(embedding) AS norm FROM chatbot.ready_document_chunks''').fetchall()
        assert ready,'No ready entries to validate'
        for row in ready:
            assert row['dimensions']==384 and abs(row['norm']-1)<1e-5
            assert row['original_text'] in row['embedding_text']
            assert row['content_hash']==row['embedded_content_hash']
            assert row['semantic_metadata']['generator_model']=='openai/gpt-oss-120b'
        row=ready[0]
        before=conn.execute('SELECT count(*) AS n FROM chatbot.document_chunk_history WHERE chunk_id=%s',(row['id'],)).fetchone()['n']
        conn.execute('UPDATE chatbot.document_chunks SET original_text=original_text||%s WHERE id=%s',
                     ('\nTest amendment (rolled back)',row['id']))
        edited=conn.execute('SELECT * FROM chatbot.document_chunks WHERE id=%s',(row['id'],)).fetchone()
        assert edited['status']=='pending' and edited['embedding'] is None and edited['semantic_metadata'] is None
        assert edited['revision']==row['revision']+1 and edited['manual_override']
        assert conn.execute('SELECT count(*) AS n FROM chatbot.document_chunk_history WHERE chunk_id=%s',(row['id'],)).fetchone()['n']==before+1
        assert not conn.execute('SELECT 1 FROM chatbot.ready_document_chunks WHERE id=%s',(row['id'],)).fetchone()
        conn.rollback()
        print(json.dumps({'ready_vectors_verified':len(ready),'text_vector_consistency':'PASS',
                          'edit_invalidation_history_exclusion_rollback':'PASS'},indent=2))


if __name__=='__main__':
    main()