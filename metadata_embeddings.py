"""Local, revision-pinned E5 embeddings. Never truncates input or downloads models."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("HF_HOME", str(ROOT / ".cache" / "huggingface"))
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
MODEL = "intfloat/multilingual-e5-small"


def pack_text(text, prefix, token_count, limit=480):
    """Pack complete lines; recursively split an oversized line without losing text."""
    def parts(value):
        if token_count(prefix + value) <= limit:
            return [value]
        if len(value) < 2:
            raise ValueError("Context leaves no room for metadata")
        middle = len(value) // 2
        boundary = value.rfind(" ", 0, middle + 1)
        cut = boundary + 1 if boundary > 0 else middle
        return parts(value[:cut]) + parts(value[cut:])

    chunks, current = [], ""
    for line in text.splitlines(keepends=True):
        for part in parts(line):
            if current and token_count(prefix + current + part) > limit:
                chunks.append(prefix + current)
                current = ""
            current += part
    if current:
        chunks.append(prefix + current)
    return chunks


class Embedder:
    def __init__(self):
        import torch
        from transformers import AutoModel, AutoTokenizer

        cache = ROOT / ".cache/huggingface/hub/models--intfloat--multilingual-e5-small"
        self.revision = (cache / "refs/main").read_text().strip()
        self.path = cache / "snapshots" / self.revision
        self.torch = torch
        torch.set_num_threads(2)
        self.tokenizer = AutoTokenizer.from_pretrained(self.path, local_files_only=True)
        self.model = AutoModel.from_pretrained(
            self.path, local_files_only=True, use_safetensors=True
        ).to("cpu").eval()

    def token_count(self, text):
        return len(self.tokenizer.encode("passage: " + text, verbose=False))

    def encode(self, texts, query=False):
        torch = self.torch
        vectors = []
        with torch.inference_mode():
            for start in range(0, len(texts), 2):
                batch = self.tokenizer(
                    [("query: " if query else "passage: ") + text
                     for text in texts[start:start+2]],
                    padding=True, truncation=False, return_tensors="pt",
                )
                if batch['input_ids'].shape[1] > 512:
                    raise ValueError("Embedding input exceeds 512 tokens; no truncation allowed")
                hidden = self.model(**batch).last_hidden_state
                mask = batch['attention_mask'].unsqueeze(-1)
                pooled = (hidden * mask).sum(1) / mask.sum(1)
                normalized = torch.nn.functional.normalize(pooled, p=2, dim=1)
                if normalized.shape[1] != 384 or not torch.isfinite(normalized).all():
                    raise ValueError("Invalid embedding dimensions or values")
                if not torch.allclose(normalized.norm(dim=1), torch.ones(len(normalized)), atol=1e-5):
                    raise ValueError("Embedding is not unit normalized")
                vectors.extend(normalized.tolist())
        return vectors