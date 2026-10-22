"""Read-only live retrieval/Groq smoke check; never prints credentials or provider errors."""
from pathlib import Path
import sys

from dotenv import dotenv_values

from source_search import search, search_connection
from source_explanation import SourceExplainer, add_explanation


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    config = dotenv_values(Path(__file__).with_name('.env'), interpolate=False)
    question = 'now tell me what i can do if police arreest me wiihtou any notice'
    with search_connection(config['retrieval_db_url']) as db:
        data = search(db, question)
    print('Retrieved:', len(data['hits']), 'passages; terms:', ', '.join(data['terms']))
    for hit in data['hits']:
        print('Source:', hit['title'], '; pages:', hit['page_start'], '-', hit['page_end'])
    data = add_explanation(data, SourceExplainer(config['groq_api'],
                           config.get('GROQ_MODEL') or 'openai/gpt-oss-120b'))
    print('Analysis status:', data['explanation_status'])
    if data['explanation_status'] != 'available':
        return 1
    points = data['explanation']['points']
    print('Validated points:', len(points), '; sections:', ', '.join(sorted({p.get('kind', 'analysis') for p in points})))
    for point in points:
        print(point.get('kind', 'analysis') + ':', point['text'])
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print('Smoke check failed:', type(error).__name__, '(details withheld)')
        sys.exit(1)