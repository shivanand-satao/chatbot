import hashlib

import httpx
from django.conf import settings

from .models import ChatbotDocument


def embed_text(text: str) -> list[float]:
    if settings.EMBEDDING_PROVIDER != "openai" or not settings.OPENAI_API_KEY:
        raise RuntimeError("An OpenAI-compatible embedding provider is required for reindexing")
    response = httpx.post(
        f"{settings.OPENAI_BASE_URL.rstrip('/')}/embeddings",
        headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
        json={"model": settings.EMBEDDING_MODEL, "input": text},
        timeout=30,
    )
    response.raise_for_status()
    vector = response.json()["data"][0]["embedding"]
    if len(vector) != settings.EMBEDDING_DIMENSIONS:
        raise RuntimeError("Embedding dimension does not match the chatbot_documents migration")
    return vector


def upsert_document(*, source_key: str, title: str, content: str, metadata: dict) -> ChatbotDocument:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    embedding = embed_text(content)
    document, _ = ChatbotDocument.objects.update_or_create(
        namespace="crime-records",
        source_type="dataset_schema",
        source_key=source_key,
        defaults={
            "title": title,
            "content": content,
            "embedding": embedding,
            "embedding_model": settings.EMBEDDING_MODEL,
            "metadata": metadata,
            "content_hash": digest,
        },
    )
    return document
