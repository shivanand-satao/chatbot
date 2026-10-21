"""Local evidence search CLI; never invokes Groq or generates SQL."""

import argparse
import json

from dotenv import dotenv_values

from metadata_embeddings import ROOT, Embedder
from retrieval import Retriever, read_connection
from document_context import build_document_context


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['datasets', 'documents'])
    parser.add_argument('question')
    parser.add_argument('--limit', type=int, default=5)
    parser.add_argument('--document-id', type=int)
    parser.add_argument('--version-label')
    parser.add_argument('--section')
    parser.add_argument('--context', action='store_true', help='Assemble document evidence only; no AI answer')
    parser.add_argument('--context-max-chars', type=int, default=12000)
    args = parser.parse_args()
    try:
        config = dotenv_values(ROOT / '.env', interpolate=False)
        embedder = Embedder()
        with read_connection(config['db_url']) as conn:
            retriever = Retriever(conn, embedder)
            if args.mode == 'datasets':
                if args.context or any(value is not None for value in (args.document_id, args.version_label, args.section)):
                    parser.error('Document filters apply only to documents mode')
                result = retriever.datasets(args.question, args.limit)
            else:
                result = retriever.documents(args.question, args.limit, args.document_id,
                                             args.version_label, args.section)
                if args.context:
                    result = build_document_context(result, args.context_max_chars)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as error:
        print(f'Search failed ({type(error).__name__}); connection/query details withheld.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())