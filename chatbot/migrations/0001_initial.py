from django.db import migrations, models
import django.db.models.deletion
import uuid
from pgvector.django import HnswIndex, VectorExtension, VectorField


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        VectorExtension(),
        migrations.CreateModel(
            name="ChatSession",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("principal_type", models.CharField(max_length=16)),
                ("principal_id", models.BigIntegerField()),
                ("title", models.CharField(blank=True, default="", max_length=200)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "db_table": "chatbot_sessions",
                "ordering": ["-updated_at"],
            },
        ),
        migrations.CreateModel(
            name="ChatbotDocument",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("namespace", models.CharField(default="crime-records", max_length=100)),
                ("source_type", models.CharField(max_length=50)),
                ("source_key", models.CharField(max_length=200)),
                ("title", models.CharField(max_length=300)),
                ("content", models.TextField()),
                ("embedding", VectorField(dimensions=1536)),
                ("embedding_model", models.CharField(max_length=100)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("content_hash", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "chatbot_documents"},
        ),
        migrations.CreateModel(
            name="ChatMessage",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("sequence", models.PositiveBigIntegerField()),
                ("role", models.CharField(choices=[("user", "User"), ("assistant", "Assistant"), ("system", "System"), ("tool", "Tool")], max_length=16)),
                ("content", models.TextField()),
                ("plan", models.JSONField(blank=True, null=True)),
                ("citations", models.JSONField(blank=True, null=True)),
                ("request_id", models.UUIDField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="messages", to="chatbot.chatsession")),
            ],
            options={"db_table": "chatbot_messages", "ordering": ["sequence"]},
        ),
        migrations.CreateModel(
            name="DatasetCatalogEntry",
            fields=[
                ("table_name", models.CharField(max_length=128, primary_key=True, serialize=False)),
                ("crime_category", models.TextField(blank=True, null=True)),
                ("row_count", models.IntegerField(blank=True, null=True)),
                ("semantic_summary", models.TextField(blank=True, null=True)),
                ("embedding_text", models.TextField(blank=True, null=True)),
                ("columns_schema", models.JSONField(blank=True, default=list)),
                ("sample_values", models.JSONField(blank=True, default=dict)),
                ("updated_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"db_table": "chatbot_schema_catalog", "managed": False},
        ),
        migrations.AddIndex(
            model_name="chatsession",
            index=models.Index(fields=["principal_type", "principal_id", "-updated_at"], name="chat_session_owner_idx"),
        ),
        migrations.AddConstraint(
            model_name="chatsession",
            constraint=models.CheckConstraint(check=models.Q(principal_type__in=["user", "admin"]), name="chat_session_principal_type_chk"),
        ),
        migrations.AddConstraint(
            model_name="chatmessage",
            constraint=models.UniqueConstraint(fields=("session", "sequence"), name="chat_message_session_sequence_uniq"),
        ),
        migrations.AddConstraint(
            model_name="chatbotdocument",
            constraint=models.UniqueConstraint(fields=("namespace", "source_type", "source_key"), name="chat_doc_source_uniq"),
        ),
        migrations.AddIndex(
            model_name="chatbotdocument",
            index=HnswIndex(
                name="chat_doc_embedding_hnsw",
                fields=["embedding"],
                opclasses=["vector_cosine_ops"],
            ),
        ),
    ]
