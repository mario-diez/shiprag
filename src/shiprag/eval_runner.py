"""Evaluación offline del RAG: recuperación, citas, abstención."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shiprag.core.config import AppConfig, load_config
from shiprag.core.schemas import AnswerStatus, QueryRequest, ResponseMode
from shiprag.ingest.pipeline import IngestPipeline
from shiprag.orchestration.pipeline import Orchestrator


def _ensure_sample_index(cfg: AppConfig) -> None:
    sample = cfg.resolve(cfg.paths.sample_dir)
    # Si no hay zonas indexadas, ingerir sample
    orch = Orchestrator(cfg)
    if not orch.index.zones_with_data():
        IngestPipeline(cfg, index=orch.index).ingest_dir(sample)


def run_eval(golden_path: Path, cfg: AppConfig | None = None) -> dict[str, Any]:
    cfg = cfg or load_config()
    _ensure_sample_index(cfg)
    orch = Orchestrator(cfg)

    rows = []
    with Path(golden_path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    results = []
    n = len(rows) or 1
    zone_ok = 0
    cite_ok = 0
    abstain_ok = 0
    abstain_total = 0
    answer_hit = 0
    retrieval_hit = 0

    for row in rows:
        req = QueryRequest(
            query=row["query"],
            emergency=bool(row.get("emergency")),
            mode=ResponseMode.EXTRACTIVE if row.get("emergency") else ResponseMode.SEMI,
        )
        resp = orch.query(req)
        expected_zone = row.get("expected_zone")
        z_hit = True
        if expected_zone:
            z_hit = expected_zone in (resp.zones_used or []) or expected_zone in (
                resp.trace.routed_zones if resp.trace else []
            )
            zone_ok += int(z_hit)

        doc_sub = row.get("expected_doc_id_contains")
        r_hit = True
        if doc_sub:
            docs = {c.doc_id for c in resp.citations} | {
                (resp.trace.selected[i]["doc_id"] if resp.trace else "")
                for i in range(len(resp.trace.selected) if resp.trace else 0)
            }
            r_hit = any(doc_sub in d for d in docs)
            retrieval_hit += int(r_hit)

        c_hit = True
        if row.get("must_cite"):
            c_hit = len(resp.citations) > 0 and resp.status in {
                AnswerStatus.OK,
                AnswerStatus.CONFLICT,
                AnswerStatus.CLARIFY,
            }
            cite_ok += int(c_hit)

        if row.get("should_abstain"):
            abstain_total += 1
            a_hit = resp.status in {AnswerStatus.ABSTAIN, AnswerStatus.NOT_FOUND}
            abstain_ok += int(a_hit)
        else:
            a_hit = True

        contains = row.get("expected_answer_contains") or []
        ans_ok = True
        if contains and not row.get("should_abstain"):
            low = resp.answer.lower()
            # también buscar en citas
            cite_text = " ".join(c.quote for c in resp.citations).lower()
            blob = low + " " + cite_text
            ans_ok = all(x.lower() in blob for x in contains)
            answer_hit += int(ans_ok)
        elif not row.get("should_abstain"):
            answer_hit += 1

        results.append(
            {
                "id": row["id"],
                "status": resp.status.value,
                "zone_ok": z_hit,
                "retrieval_ok": r_hit,
                "cite_ok": c_hit,
                "abstain_ok": a_hit,
                "answer_ok": ans_ok,
                "confidence": resp.confidence,
                "zones": resp.zones_used,
            }
        )

    answerable = sum(1 for r in rows if not r.get("should_abstain")) or 1
    zoned = sum(1 for r in rows if r.get("expected_zone")) or 1
    must_cite_n = sum(1 for r in rows if r.get("must_cite")) or 1
    retrieval_n = sum(1 for r in rows if r.get("expected_doc_id_contains")) or 1

    report = {
        "n": len(rows),
        "metrics": {
            "zone_routing_accuracy": round(zone_ok / zoned, 4),
            "retrieval_doc_hit_rate": round(retrieval_hit / retrieval_n, 4),
            "citation_rate_on_answerable": round(cite_ok / must_cite_n, 4),
            "answer_contains_rate": round(answer_hit / answerable, 4),
            "correct_abstention_rate": round(abstain_ok / abstain_total, 4)
            if abstain_total
            else None,
        },
        "cases": results,
    }
    out = cfg.log_path / "last_eval.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
