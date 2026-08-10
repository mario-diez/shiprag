"""Script de descarga EN PUERTO (requiere internet una sola vez).

Uso:
  python scripts/download_models.py --profile home
  python scripts/download_models.py --profile server

Los pesos quedan en models/ para operación air-gapped.
El perfil lite NO descarga nada (no lo necesita).
"""

from __future__ import annotations

import argparse
from pathlib import Path

PROFILE_MODELS = {
    "home": {
        "embeddings": "intfloat/multilingual-e5-small",
        "reranker": "cross-encoder/ms-marco-MiniLM-L-6-v2",
        "verifier": "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli",
    },
    "balanced": {
        # Mismos pesos que workstation; perfil balanced usa menos VRAM (sin LLM).
        "embeddings": "intfloat/multilingual-e5-base",
        "reranker": "BAAI/bge-reranker-base",
        "verifier": "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli",
    },
    "workstation": {
        "embeddings": "intfloat/multilingual-e5-base",
        "reranker": "BAAI/bge-reranker-base",
        "verifier": "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli",
        # LLM GGUF: no se descarga por HF aquí; ver docs/MODELS_WORKSTATION.md
    },
    "server": {
        # Tier producción a bordo (distinto de workstation).
        "embeddings": "BAAI/bge-m3",
        "reranker": "BAAI/bge-reranker-v2-m3",
        "verifier": "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli",
        # LLM GGUF Qwen3-32B: descarga manual; ver docs/MODELS_WORKSTATION.md
    },
}


def _save_st(name: str, dest: Path) -> None:
    from sentence_transformers import SentenceTransformer

    dest.mkdir(parents=True, exist_ok=True)
    print(f"Descargando embeddings {name}")
    m = SentenceTransformer(name)
    m.save(str(dest))
    print("→", dest)


def _save_ce(name: str, dest: Path) -> None:
    from sentence_transformers import CrossEncoder

    dest.mkdir(parents=True, exist_ok=True)
    print(f"Descargando reranker {name}")
    try:
        ce = CrossEncoder(name)
        if hasattr(ce, "save"):
            ce.save(str(dest))
        else:
            ce.model.save_pretrained(str(dest))
            if hasattr(ce, "tokenizer"):
                ce.tokenizer.save_pretrained(str(dest))
        print("→", dest)
    except Exception as exc:
        print("Aviso reranker:", exc)
        print("Puede copiar manualmente el modelo a", dest)


def _save_verifier(name: str, dest: Path) -> None:
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    dest.mkdir(parents=True, exist_ok=True)
    print(f"Descargando verifier NLI {name}")
    try:
        tok = AutoTokenizer.from_pretrained(name)
        model = AutoModelForSequenceClassification.from_pretrained(name)
        tok.save_pretrained(str(dest))
        model.save_pretrained(str(dest))
        print("→", dest)
    except Exception as exc:
        print("Aviso verifier NLI:", exc)
        print("Puede copiar manualmente el modelo a", dest)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile",
        choices=["home", "balanced", "workstation", "server"],
        default="home",
        help="Qué juego de modelos descargar (lite no usa modelos)",
    )
    parser.add_argument("--out", default="models", help="Directorio destino")
    parser.add_argument(
        "--skip-verifier",
        action="store_true",
        help="No descargar el modelo NLI de grounding",
    )
    args = parser.parse_args()
    specs = PROFILE_MODELS[args.profile]
    out = Path(args.out)

    emb_name = specs["embeddings"]
    rer_name = specs["reranker"]
    emb_dir = out / "embeddings" / Path(emb_name).name
    rer_dir = out / "reranker" / Path(rer_name).name

    _save_st(emb_name, emb_dir)
    _save_ce(rer_name, rer_dir)

    if not args.skip_verifier and specs.get("verifier"):
        ver_name = specs["verifier"]
        # Carpeta alineada con config: mDeBERTa-v3-base-mnli-xnli
        ver_dir = out / "verifier" / "mDeBERTa-v3-base-mnli-xnli"
        _save_verifier(ver_name, ver_dir)

    print("Listo. Arranque con:")
    print(f"  shiprag --profile {args.profile} ingest data/sample")
    print(f"  shiprag --profile {args.profile} serve")
    print("En navegación NO se necesita red.")
    if args.profile == "server":
        print(
            "Nota server: LLM Qwen3-32B Q4_K_M GGUF hay que copiarlo a "
            "models/llm/qwen3-32b-instruct-q4_k_m.gguf (fallback: Qwen2.5-7B)."
        )


if __name__ == "__main__":
    main()
