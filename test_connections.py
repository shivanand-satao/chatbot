"""Test the local .env credentials with one small Groq call and a read-only query."""

import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

import psycopg
from dotenv import dotenv_values
from groq import Groq


def safe_error(error: Exception, secrets: list[str]) -> str:
    text = str(error)
    for secret in sorted(filter(None, secrets), key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    return f"{type(error).__name__}: {text}"


def main() -> int:
    env_path = Path(__file__).resolve().with_name(".env")
    if not env_path.is_file():
        print("[FAIL] Missing .env beside this script.")
        return 1
    config = dotenv_values(env_path, interpolate=False)
    api_key = (config.get("groq_api") or "").strip()
    db_url = (config.get("db_url") or "").strip()
    secrets = [api_key, db_url]
    try:
        password = urlsplit(db_url).password
        if password:
            secrets.extend([password, unquote(password)])
    except ValueError:
        pass
    passed = 0

    try:
        if not api_key:
            raise ValueError("groq_api is missing or empty in .env")
        model = config.get("GROQ_MODEL") or "openai/gpt-oss-120b"
        with Groq(api_key=api_key, timeout=20.0, max_retries=0) as client:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Reply with OK."}],
                max_completion_tokens=128,
                reasoning_effort="low",
                temperature=0,
            )
        if not response.choices or not response.choices[0].message.content:
            raise RuntimeError("Groq returned no text")
        print(f"[PASS] Groq ({model}): authenticated and generated a response.")
        passed += 1
    except Exception as error:
        print(f"[FAIL] Groq: {safe_error(error, secrets)}")

    try:
        if not db_url:
            raise ValueError("db_url is missing or empty in .env")
        if urlsplit(db_url).scheme not in {"postgres", "postgresql"}:
            raise ValueError("db_url must be a PostgreSQL connection URL")
        with psycopg.connect(
            db_url,
            connect_timeout=10,
            sslmode="require",
            options="-c statement_timeout=10000 -c default_transaction_read_only=on",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                if cursor.fetchone() != (1,):
                    raise RuntimeError("Unexpected SELECT 1 result")
        print("[PASS] Supabase: connected over SSL and SELECT 1 succeeded.")
        passed += 1
    except Exception as error:
        print(f"[FAIL] Supabase: {safe_error(error, secrets)}")

    print(f"\n{passed}/2 connection tests passed.")
    return 0 if passed == 2 else 1


if __name__ == "__main__":
    sys.exit(main())