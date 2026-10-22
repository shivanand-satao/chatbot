"""Read-only live indexing counts. No Groq calls or indexing worker launches."""

import argparse
import time
from datetime import datetime
from pathlib import Path

import psycopg
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parent


def read_counts():
    config = dotenv_values(ROOT / '.env', interpolate=False)
    with psycopg.connect(
        config['db_url'], sslmode='require', connect_timeout=5,
        options='-c default_transaction_read_only=on -c statement_timeout=5000',
    ) as connection:
        return dict(connection.execute(
            'SELECT status,count(*) FROM chatbot.document_chunks GROUP BY status'
        ).fetchall())


def progress_line(counts):
    ready = counts.get('ready', 0)
    pending = counts.get('pending', 0)
    total = sum(counts.values())
    percent = 100 * ready / total if total else 0
    other = total - ready - pending
    return (f'Ready: {ready:,} | Remaining (pending): {pending:,} | '
            f'Total: {total:,} | {percent:.2f}% ready | Other statuses: {other:,}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--interval', type=int, default=5)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if not 2 <= args.interval <= 3600:
        parser.error('Interval must be between 2 and 3600 seconds')
    print(f'Live database counts every {args.interval}s. Ctrl+C stops ONLY this monitor.', flush=True)
    try:
        while True:
            try:
                counts = read_counts()
                print(f'[{datetime.now():%H:%M:%S}] {progress_line(counts)}', flush=True)
            except Exception as error:
                # Never print database connection strings or exception details.
                print(f'Counts unavailable ({type(error).__name__}); indexing is unaffected.', flush=True)
                if args.once:
                    return 1
            if args.once:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print('\nMonitor stopped; indexing was not stopped.', flush=True)
        return 0


if __name__ == '__main__':
    raise SystemExit(main())