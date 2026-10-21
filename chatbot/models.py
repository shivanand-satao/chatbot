import uuid

from django.db import models
from pgvector.django import HnswIndex, VectorField


class ChatSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    principal_type = models.CharField(max_length=16)
    principal_id = models.BigIntegerField()
    title = models.CharField(max_length=200, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "chatbot_sessions"
        ordering = ["-updated_at"]
        indexes = [
            models.Index(
                fields=["principal_type", "principal_id", "-updated_at"],
                name="chat_session_owner_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(principal_type__in=["user", "admin"]),
                name="chat_session_principal_type_chk",
            )
        ]


class ChatMessage(models.Model):
    ROLE_CHOICES = (
        ("user", "User"),
        ("assistant", "Assistant"),
        ("system", "System"),
        ("tool", "Tool"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sequence = models.PositiveBigIntegerField()
    role = models.CharField(max_length=16, choices=ROLE_CHOICES)
    content = models.TextField()
    plan = models.JSONField(null=True, blank=True)
    citations = models.JSONField(null=True, blank=True)
    request_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "chatbot_messages"
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "sequence"],
                name="chat_message_session_sequence_uniq",
            )
        ]


class ChatbotDocument(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    namespace = models.CharField(max_length=100, default="crime-records")
    source_type = models.CharField(max_length=50)
    source_key = models.CharField(max_length=200)
    title = models.CharField(max_length=300)
    content = models.TextField()
    embedding = VectorField(dimensions=1536)
    embedding_model = models.CharField(max_length=100)
    metadata = models.JSONField(default=dict, blank=True)
    content_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "chatbot_documents"
        constraints = [
            models.UniqueConstraint(
                fields=["namespace", "source_type", "source_key"],
                name="chat_doc_source_uniq",
            )
        ]
        indexes = [
            HnswIndex(
                name="chat_doc_embedding_hnsw",
                fields=["embedding"],
                opclasses=["vector_cosine_ops"],
            )
        ]


class DatasetCatalogEntry(models.Model):
    """Read-only projection of the existing backend catalog table."""

    table_name = models.CharField(max_length=128, primary_key=True)
    crime_category = models.TextField(null=True, blank=True)
    row_count = models.IntegerField(null=True, blank=True)
    semantic_summary = models.TextField(null=True, blank=True)
    embedding_text = models.TextField(null=True, blank=True)
    columns_schema = models.JSONField(default=list, blank=True)
    sample_values = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "chatbot_schema_catalog"
