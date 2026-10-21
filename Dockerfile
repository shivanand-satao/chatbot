FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY config ./config
COPY chatbot ./chatbot
COPY manage.py ./

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ".[dev]"

EXPOSE 8001

CMD ["uvicorn", "config.asgi:application", "--host", "0.0.0.0", "--port", "8001"]
