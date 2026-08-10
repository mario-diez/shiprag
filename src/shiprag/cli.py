"""CLI local: ingest / query / serve / eval / profiles."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from shiprag.core.config import clear_config_cache, list_profiles, load_config
from shiprag.core.logging import setup_logging
from shiprag.core.schemas import QueryRequest, ResponseMode, Zone


def _cfg(args: argparse.Namespace):
    clear_config_cache()
    return load_config(config_path=args.config, profile=getattr(args, "profile", None))


def cmd_ingest(args: argparse.Namespace) -> int:
    from shiprag.ingest.pipeline import IngestPipeline

    cfg = _cfg(args)
    setup_logging(cfg)
    print(json.dumps({"runtime": cfg.runtime_summary()}, ensure_ascii=False), file=sys.stderr)
    pipe = IngestPipeline(cfg)
    zone = Zone(args.zone) if args.zone else None
    path = Path(args.path)
    if path.is_dir():
        results = pipe.ingest_dir(path, zone=zone)
    else:
        results = [pipe.ingest_file(path, zone=zone)]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    from shiprag.orchestration.pipeline import Orchestrator

    cfg = _cfg(args)
    setup_logging(cfg)
    orch = Orchestrator(cfg)
    req = QueryRequest(
        query=args.query,
        zone=Zone(args.zone) if args.zone else None,
        mode=ResponseMode(args.mode),
        emergency=bool(args.emergency) or args.mode in {"extractive", "citations_only"},
    )
    if args.emergency:
        req.emergency = True
    resp = orch.query(req)
    print(json.dumps(resp.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import os

    import uvicorn

    cfg = _cfg(args)
    setup_logging(cfg)
    # Propagar perfil al proceso de uvicorn (el módulo app recarga config)
    if getattr(args, "profile", None):
        os.environ["SHIPRAG_PROFILE"] = args.profile
    if args.config:
        os.environ["SHIPRAG_CONFIG"] = args.config
    host = args.host or cfg.api.host
    port = args.port or cfg.api.port
    print(json.dumps({"runtime": cfg.runtime_summary(), "url": f"http://{host}:{port}"}, ensure_ascii=False))
    uvicorn.run(
        "shiprag.api.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=False,
        log_level=cfg.logging.level.lower(),
    )
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    from shiprag.eval_runner import run_eval

    cfg = _cfg(args)
    setup_logging(cfg)
    report = run_eval(Path(args.golden), cfg=cfg)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    """Comprobación rápida para PC de casa: ingest check + queries clave."""
    from shiprag.eval_runner import run_eval
    from shiprag.ingest.pipeline import IngestPipeline
    from shiprag.orchestration.pipeline import Orchestrator
    from shiprag.core.schemas import AnswerStatus

    cfg = _cfg(args)
    setup_logging(cfg)
    print(json.dumps({"runtime": cfg.runtime_summary()}, ensure_ascii=False))

    sample = cfg.resolve(cfg.paths.sample_dir)
    if not cfg.index_path.exists() or not any(cfg.index_path.iterdir()):
        IngestPipeline(cfg).ingest_dir(sample)

    orch = Orchestrator(cfg)
    checks = [
        ("procedimiento hombre al agua", True, {AnswerStatus.OK, AnswerStatus.CLARIFY, AnswerStatus.CONFLICT}),
        ("alarma FO-12 del generador", False, {AnswerStatus.OK, AnswerStatus.CLARIFY, AnswerStatus.CONFLICT}),
        ("receta de paella valenciana", False, {AnswerStatus.NOT_FOUND, AnswerStatus.ABSTAIN}),
    ]
    results = []
    ok_all = True
    for q, emergency, allowed in checks:
        resp = orch.query(QueryRequest(query=q, emergency=emergency, mode=ResponseMode.AUTO))
        passed = resp.status in allowed
        ok_all = ok_all and passed
        results.append(
            {
                "query": q,
                "status": resp.status.value,
                "pass": passed,
                "citations": len(resp.citations),
                "confidence": resp.confidence,
            }
        )

    golden = Path("eval/golden_set.jsonl")
    eval_metrics = None
    if golden.exists() and not args.skip_eval:
        eval_metrics = run_eval(golden, cfg=cfg).get("metrics")

    report = {"ok": ok_all, "checks": results, "eval_metrics": eval_metrics}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok_all else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    from shiprag.doctor import run_doctor

    cfg = _cfg(args)
    report = run_doctor(cfg)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


def cmd_profiles(args: argparse.Namespace) -> int:
    print(json.dumps(list_profiles(), ensure_ascii=False, indent=2))
    return 0


def cmd_runtime(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    print(json.dumps(cfg.runtime_summary(), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="shiprag",
        description="ShipRAG offline — use --profile lite|home|balanced|workstation|server",
    )
    p.add_argument("--config", default=None, help="Ruta a config YAML base")
    p.add_argument(
        "--profile",
        default=None,
        choices=["lite", "home", "balanced", "workstation", "server"],
        help="Perfil de hardware (default: lite vía config/env SHIPRAG_PROFILE)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest", help="Ingestar documento(s)")
    ing.add_argument("path", help="Archivo o directorio")
    ing.add_argument("--zone", default=None, help="Zona forzada")
    ing.set_defaults(func=cmd_ingest)

    q = sub.add_parser("query", help="Consultar el sistema")
    q.add_argument("query", help="Pregunta")
    q.add_argument("--zone", default=None)
    q.add_argument(
        "--mode",
        default="auto",
        choices=[m.value for m in ResponseMode],
    )
    q.add_argument("--emergency", action="store_true")
    q.set_defaults(func=cmd_query)

    s = sub.add_parser("serve", help="API + UI local")
    s.add_argument("--host", default=None)
    s.add_argument("--port", type=int, default=None)
    s.set_defaults(func=cmd_serve)

    e = sub.add_parser("eval", help="Evaluar contra golden set")
    e.add_argument("golden", help="JSONL de evaluación")
    e.set_defaults(func=cmd_eval)

    sm = sub.add_parser("smoke", help="Smoke test rápido (PC casa)")
    sm.add_argument("--skip-eval", action="store_true")
    sm.set_defaults(func=cmd_smoke)

    sub.add_parser("doctor", help="Diagnóstico de entorno/perfil").set_defaults(func=cmd_doctor)
    sub.add_parser("profiles", help="Listar perfiles disponibles").set_defaults(func=cmd_profiles)
    sub.add_parser("runtime", help="Mostrar perfil/backends activos").set_defaults(func=cmd_runtime)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        code = args.func(args)
    except KeyboardInterrupt:
        code = 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
    sys.exit(code)


if __name__ == "__main__":
    main()
