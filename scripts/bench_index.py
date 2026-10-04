"""Benchmark reproducible del índice local (sin modelos ni GPU).

Mide cuánto cuesta ingerir N documentos sintéticos y cuánto tarda una consulta
BM25 + vectorial, para dimensionar hasta dónde llega el almacenamiento embebido
(Chroma + rank_bm25) antes de necesitar un motor de búsqueda dedicado.

Modos de ingesta:
  per_file  un upsert por documento, publicando al momento (lo que hace la API
            con cada subida)
  batch     todos los documentos dentro de HybridIndex.batch() (lo que hace
            `shiprag ingest <dir>`); solo si la versión del código lo soporta

Uso:
  python scripts/bench_index.py --docs 50 100 250 500 --chunks-per-doc 20
  python scripts/bench_index.py --docs 1000 --modes batch --out bench.json

Para comparar con otra versión del código, ejecútalo con su `src` en PYTHONPATH.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path

from shiprag.core.config import PathsConfig, clear_config_cache, load_config
from shiprag.core.schemas import Chunk, Zone
from shiprag.index.embeddings import HashEmbedding
from shiprag.index.store import HybridIndex

ZONES = [Zone.MAQUINARIA, Zone.PUENTE, Zone.SEGURIDAD, Zone.CUBIERTA]


def make_doc(rng: random.Random, vocab: list[str], doc_n: int, chunks: int, words: int):
    zone = ZONES[doc_n % len(ZONES)]
    return [
        Chunk(
            chunk_id=f"bench{doc_n}_{i}",
            doc_id=f"bench{doc_n}",
            text=" ".join(rng.choices(vocab, k=words)),
            page_start=i + 1,
            page_end=i + 1,
            zone=zone,
            title=f"Documento {doc_n}",
        )
        for i in range(chunks)
    ]


def run(n_docs: int, mode: str, args: argparse.Namespace) -> dict | None:
    clear_config_cache()
    base = load_config(profile="lite")
    tmp = Path(tempfile.mkdtemp(prefix=f"shiprag_bench_{mode}_{n_docs}_"))
    cfg = base.model_copy(deep=True)
    cfg.paths = PathsConfig(
        data_dir=str(tmp),
        raw_dir=str(tmp / "raw"),
        index_dir=str(tmp / "idx"),
        log_dir=str(tmp / "logs"),
        models_dir=str(tmp / "models"),
        sample_dir=str(tmp),
    )
    index = HybridIndex(cfg, embedder=HashEmbedding(dim=384))
    if mode == "batch" and not hasattr(index, "batch"):
        return None

    rng = random.Random(args.seed)
    vocab = [f"termino{i}" for i in range(args.vocab)]
    docs = [make_doc(rng, vocab, d, args.chunks_per_doc, args.words) for d in range(n_docs)]

    t0 = time.perf_counter()
    if mode == "batch":
        with index.batch():
            for chunks in docs:
                index.upsert_chunks(chunks)
    else:
        for chunks in docs:
            index.upsert_chunks(chunks)
    ingest_s = time.perf_counter() - t0

    lat_lex: list[float] = []
    lat_dense: list[float] = []
    for _ in range(args.queries):
        q = " ".join(rng.choices(vocab, k=5))
        t1 = time.perf_counter()
        index.lexical(Zone.GENERAL).search(q, top_k=40)
        lat_lex.append((time.perf_counter() - t1) * 1000)
        t1 = time.perf_counter()
        index.dense(Zone.GENERAL).search(q, top_k=40)
        lat_dense.append((time.perf_counter() - t1) * 1000)

    t2 = time.perf_counter()
    HybridIndex(cfg, embedder=HashEmbedding(dim=384)).lexical(Zone.GENERAL)
    reload_s = time.perf_counter() - t2
    return {
        "mode": mode,
        "docs": n_docs,
        "chunks": n_docs * args.chunks_per_doc,
        "ingest_s": round(ingest_s, 2),
        "ms_per_doc": round(ingest_s * 1000 / max(1, n_docs), 1),
        "bm25_query_ms_p50": round(statistics.median(lat_lex), 1),
        "dense_query_ms_p50": round(statistics.median(lat_dense), 1),
        "reload_general_s": round(reload_s, 2),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--docs", type=int, nargs="+", default=[50, 100, 250, 500])
    p.add_argument("--chunks-per-doc", type=int, default=20)
    p.add_argument("--words", type=int, default=180, help="palabras por chunk (~1.200 chars)")
    p.add_argument("--vocab", type=int, default=20000)
    p.add_argument("--queries", type=int, default=20)
    p.add_argument("--modes", nargs="+", default=["per_file", "batch"], choices=["per_file", "batch"])
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--out", default=None, help="guardar resultados en JSON")
    args = p.parse_args()

    rows = []
    for mode in args.modes:
        for n in args.docs:
            row = run(n, mode, args)
            if row is None:
                print(f"[{mode}] no soportado por esta versión del código", file=sys.stderr)
                break
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    if args.out:
        Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
