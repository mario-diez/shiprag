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
    },
    "workstation": {
        "embeddings": "intfloat/multilingual-e5-base",
        "reranker": "BAAI/bge-reranker-base",
        # LLM GGUF: no se descarga por HF aquí; ver docs/MODELS_WORKSTATION.md
    },
    "server": {
        "embeddings": "intfloat/multilingual-e5-base",
        "reranker": "BAAI/bge-reranker-base",
    },
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile",
        choices=["home", "workstation", "server"],
        default="home",
        help="Qué juego de modelos descargar (lite no usa modelos)",
    )
    parser.add_argument("--out", default="models", help="Directorio destino")
    args = parser.parse_args()
    specs = PROFILE_MODELS[args.profile]
    out = Path(args.out)

    emb_name = specs["embeddings"]
    rer_name = specs["reranker"]
    emb_dir = out / "embeddings" / Path(emb_name).name
    rer_dir = out / "reranker" / Path(rer_name).name
    emb_dir.mkdir(parents=True, exist_ok=True)
    rer_dir.mkdir(parents=True, exist_ok=True)

    print(f"Perfil {args.profile}: descargando embeddings {emb_name}")
    from sentence_transformers import SentenceTransformer

    m = SentenceTransformer(emb_name)
    m.save(str(emb_dir))
    print("→", emb_dir)

    print(f"Perfil {args.profile}: descargando reranker {rer_name}")
    from sentence_transformers import CrossEncoder

    try:
        ce = CrossEncoder(rer_name)
        if hasattr(ce, "save"):
            ce.save(str(rer_dir))
        else:
            ce.model.save_pretrained(str(rer_dir))
            if hasattr(ce, "tokenizer"):
                ce.tokenizer.save_pretrained(str(rer_dir))
    except Exception as exc:
        print("Aviso reranker:", exc)
        print("Puede copiar manualmente el modelo a", rer_dir)

    print("Listo. Arranque con:")
    print(f"  shiprag --profile {args.profile} ingest data/sample")
    print(f"  shiprag --profile {args.profile} serve")
    print("En navegación NO se necesita red.")


if __name__ == "__main__":
    main()
