"""Small CPU-only PDF retrieval smoke benchmark. No database or LLM calls."""

import argparse
import json
import os
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("HF_HOME", str(ROOT / ".cache" / "huggingface"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import pdfplumber
import psutil
import torch
from transformers import AutoModel, AutoTokenizer

MODEL = "intfloat/multilingual-e5-small"
LABELS = {
    "2023110156.pdf": "Code of Criminal Procedure, 1973; uploaded edition",
    "202407091369269213.pdf": "Alphabetical list of Central Acts; index only",
    "Bharatiya_Nagarik_Suraksha_Sanhita,_2023.pdf": "BNSS 2023; introduced bill, not verified current law",
    "Bharatiya_Nyaya_Sanhita,_2023.pdf": "BNS 2023; introduced bill, not verified current law",
    "indian  constitution.pdf": "Constitution of India; edition as on 1 May 2024",
    "IPC_186045.pdf": "Indian Penal Code; uploaded edition, not verified current law",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=Path, required=True)
    args = parser.parse_args()
    if not args.documents.is_dir():
        parser.error("Document directory does not exist")
    torch.set_num_threads(2)
    process = psutil.Process()
    memory = {"peak_rss_bytes": process.memory_info().rss}
    stop = threading.Event()

    def monitor():
        while not stop.wait(0.1):
            memory["peak_rss_bytes"] = max(memory["peak_rss_bytes"], process.memory_info().rss)

    threading.Thread(target=monitor, daemon=True).start()
    started = time.perf_counter()
    print("Loading tokenizer/model on CPU (first run downloads weights)...", flush=True)
    tokenizer = AutoTokenizer.from_pretrained(MODEL)
    model = AutoModel.from_pretrained(MODEL, use_safetensors=True).to("cpu").eval()
    load_seconds = time.perf_counter() - started
    print(f"Model ready in {load_seconds:.1f}s", flush=True)
    chunks, pages_report = [], []

    def add_text(text, context, source, page, kind):
        # Token windows are deliberately only a benchmark, not production chunking.
        ids = tokenizer.encode(text, add_special_tokens=False, verbose=False)
        for offset in range(0, len(ids), 300):
            body = tokenizer.decode(ids[offset:offset + 350], skip_special_tokens=True)
            value = f"passage: {context}\n{body}"
            if len(tokenizer.encode(value)) > 512:
                raise ValueError("Benchmark chunk exceeded model input limit")
            chunks.append({"source": source, "page": page, "kind": kind, "text": value})
            if offset + 350 >= len(ids):
                break

    extract_started = time.perf_counter()
    for filename, label in LABELS.items():
        path = args.documents / filename
        with pdfplumber.open(path) as pdf:
            # Three pages per file; maximum 18 pages, no full-document extraction.
            selected = sorted({0, min(1, len(pdf.pages)-1), min(19, len(pdf.pages)-1)})
            for index in selected:
                page = pdf.pages[index]
                text = page.extract_text() or ""
                tables = page.extract_tables()
                pages_report.append({"source": filename, "page": index+1,
                                     "characters": len(text), "tables_detected": len(tables),
                                     "replacement_characters": text.count("\ufffd")})
                context = f"{label}; PDF page {index+1}"
                add_text(text, context, filename, index+1, "page_text")
                for number, table in enumerate(tables):
                    if not table:
                        continue
                    header_index = 0
                    # This sampled index has a spanning title before its real header.
                    # Do not generalize this known layout to arbitrary legal tables.
                    if filename == "202407091369269213.pdf":
                        for candidate, cells in enumerate(table[:4]):
                            joined = " ".join(str(cell or "") for cell in cells).lower()
                            if "name of the act" in joined and "year" in joined:
                                header_index = candidate
                                break
                    header = " | ".join(str(cell or "").replace("\n", " ") for cell in table[header_index])
                    for row_index, row in enumerate(table[header_index+1:], start=header_index+1):
                        row_text = " | ".join(str(cell or "").replace("\n", " ") for cell in row)
                        add_text(row_text, f"{context}; table {number+1}; header: {header}; row {row_index}",
                                 filename, index+1, "table_row")
                page.close()
        print(f"Sampled {filename}", flush=True)
    extract_seconds = time.perf_counter()-extract_started
    if not chunks:
        raise RuntimeError("No sample text could be extracted")

    def embed(texts):
        result = []
        with torch.inference_mode():
            for offset in range(0, len(texts), 2):
                batch = tokenizer(texts[offset:offset+2], padding=True, truncation=False, return_tensors="pt")
                if batch["input_ids"].shape[1] > 512:
                    raise ValueError("Oversized embedding input")
                hidden = model(**batch).last_hidden_state
                mask = batch["attention_mask"].unsqueeze(-1)
                pooled = (hidden * mask).sum(1) / mask.sum(1)
                result.append(torch.nn.functional.normalize(pooled, p=2, dim=1))
        return torch.cat(result)

    embed(["query: warmup"])
    start = time.perf_counter()
    vectors = embed([chunk["text"] for chunk in chunks])
    embed_seconds = time.perf_counter()-start
    assert vectors.shape == (len(chunks), 384)
    assert torch.isfinite(vectors).all()
    assert torch.allclose(vectors.norm(dim=1), torch.ones(len(chunks)), atol=1e-5)
    queries = [
        ("Which document is an alphabetical list of Central Acts?", "202407091369269213.pdf"),
        ("Which uploaded document is the Constitution of India?", "indian  constitution.pdf"),
        ("Which document contains the Indian Penal Code?", "IPC_186045.pdf"),
        ("भारत का संविधान किस दस्तावेज़ में है?", "indian  constitution.pdf"),
    ]
    query_started = time.perf_counter()
    query_vectors = embed(["query: " + query for query, _ in queries])
    query_seconds = time.perf_counter()-query_started
    retrieval = []
    for (query, expected), vector in zip(queries, query_vectors):
        scores, indices = (vectors @ vector).topk(min(3, len(chunks)))
        hits = [{"source": chunks[i]["source"], "page": chunks[i]["page"],
                 "kind": chunks[i]["kind"], "score": round(float(score), 4)}
                for score, i in zip(scores.tolist(), indices.tolist())]
        retrieval.append({"query": query, "expected_source": expected, "top3": hits,
                          "hit_at_1": hits[0]["source"] == expected,
                          "hit_at_3": any(hit["source"] == expected for hit in hits)})
    stop.set()
    output = ROOT / "benchmark-output"
    output.mkdir(exist_ok=True)
    report = {"model": MODEL, "revision": getattr(model.config, "_commit_hash", None),
              "device": "cpu", "threads": 2, "batch_size": 2,
              "load_seconds_including_download": round(load_seconds, 2),
              "extraction_seconds": round(extract_seconds, 2),
              "embedding_seconds": round(embed_seconds, 2),
              "chunks_per_second": round(len(chunks)/embed_seconds, 2),
              "query_seconds_total": round(query_seconds, 2),
              "peak_process_rss_mb": round(memory["peak_rss_bytes"]/1024**2, 1),
              "system_ram_gb": round(psutil.virtual_memory().total/1024**3, 2),
              "dimensions": vectors.shape[1], "chunks": len(chunks),
              "pages": pages_report, "retrieval_smoke_tests": retrieval,
              "limitations": ["Tiny sampled corpus; not a legal-answer or full retrieval evaluation",
                              "Page token windows are benchmark-only; no production section chunker",
                              "Table extraction is unverified; no multi-page stitching or OCR",
                              "Document-title queries may benefit from repeated title metadata",
                              "No database writes and no full-document indexing"]}
    (output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    (output / "sample_chunks.json").write_text(json.dumps(chunks, indent=2, ensure_ascii=False), encoding="utf-8")
    torch.save(vectors, output / "sample_vectors.pt")
    print(json.dumps({key: value for key, value in report.items() if key not in {"pages", "retrieval_smoke_tests"}}, indent=2), flush=True)
    print(f"Retrieval smoke tests: {sum(r['hit_at_1'] for r in retrieval)}/{len(retrieval)} hit@1")
    print(f"Results: {output}")


if __name__ == "__main__":
    main()