from django.core.management.base import BaseCommand, CommandError

from chatbot.catalog import get_catalog
from chatbot.documents import upsert_document


class Command(BaseCommand):
    help = "Create or update pgvector documents from the existing crime schema catalog."

    def handle(self, *args, **options):
        catalog = get_catalog()
        if not catalog:
            raise CommandError("No dataset catalog or dataset tables were found")

        count = 0
        for entry in catalog:
            content = entry.get("embedding_text") or entry.get("semantic_summary") or entry["table_name"]
            upsert_document(
                source_key=entry["table_name"],
                title=entry["table_name"],
                content=content,
                metadata={
                    "table_name": entry["table_name"],
                    "crime_category": entry.get("crime_category", ""),
                    "row_count": entry.get("row_count", 0),
                },
            )
            count += 1

        self.stdout.write(self.style.SUCCESS(f"Indexed {count} dataset documents"))
