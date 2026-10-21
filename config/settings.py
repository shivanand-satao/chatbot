from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "local-only-development-key")
DEBUG = os.getenv("DJANGO_DEBUG", "false").lower() == "true"
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "chatbot",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "chatbot.middleware.ChatbotCorsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = []
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DB_SCHEMA = os.getenv("DB_SCHEMA", "public")
if not DB_SCHEMA.replace("_", "").isalnum():
    raise RuntimeError("DB_SCHEMA must contain only letters, numbers, and underscores")

DB_SSL = os.getenv("DB_SSL", "true").lower() != "false"
DB_OPTIONS = {"sslmode": "require"} if DB_SSL else {"sslmode": "disable"}
DB_OPTIONS["options"] = f"-c search_path={DB_SCHEMA},public"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME", "crime_records"),
        "USER": os.getenv("DB_USER", "postgres"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "localhost"),
        "PORT": os.getenv("DB_PORT", "5432"),
        "CONN_MAX_AGE": int(os.getenv("DB_CONN_MAX_AGE", "60")),
        "OPTIONS": DB_OPTIONS,
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
LANGUAGE_CODE = "en-us"

CHATBOT_QUERY_TIMEOUT_MS = int(os.getenv("CHATBOT_QUERY_TIMEOUT_MS", "3000"))
JWT_ACCESS_SECRET = os.getenv("JWT_ACCESS_SECRET", "")
JWT_ALGORITHMS = [
    item.strip() for item in os.getenv("JWT_ALGORITHMS", "HS256").split(",") if item.strip()
]
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "openai")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", "1536"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CHATBOT_CORS_ORIGINS", "").split(",")
    if origin.strip()
]
