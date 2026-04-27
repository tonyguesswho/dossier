"""Citation-precision scorecard over completed investigations.

    uv run python -m dossier.eval.report [--limit N] [--json]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from sqlalchemy import text

from dossier.core.db import get_engine
from dossier.eval.scorer import citation_precision
from dossier.models import Claim
from dossier.observability import load_env

logger = logging.getLogger(__name__)


def _fetch_complete_investigations(engine: Any, limit: int | None) -> list[dict]:
    # No company_name column on investigations — input_ref doubles as the label.
    sql = (
        "SELECT id::text, input_ref, completed_at "
        "FROM investigations "
        "WHERE status = 'complete' AND brief_markdown IS NOT NULL "
        "ORDER BY completed_at DESC"
    )
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    with engine.connect() as conn:
        rows = conn.execute(text(sql)).all()
    return [{"id": r[0], "company": r[1], "completed_at": r[2]} for r in rows]


def _fetch_claims_and_corpus(
    engine: Any, investigation_id: str
) -> tuple[list[Claim], dict[str, str]]:
    # grounded_span_start/end are SOURCE-absolute; sc.text is chunk-local. Translate via chunk_char_start.
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.id::text, c.claim_text, c.grounded_source_chunk_id::text, "
                "       c.grounded_span_start, c.grounded_span_end, "
                "       sc.text AS chunk_text, sc.char_start AS chunk_char_start "
                "FROM claims c "
                "LEFT JOIN source_chunks sc ON c.grounded_source_chunk_id = sc.id "
                "WHERE c.investigation_id = :inv_id"
            ),
            {"inv_id": investigation_id},
        ).all()

    claims: list[Claim] = []
    corpus: dict[str, str] = {}
    for r in rows:
        _id, claim_text, chunk_id, start, end, chunk_text, chunk_char_start = r
        if (
            chunk_id and chunk_text is not None
            and start is not None and end is not None
            and chunk_char_start is not None
        ):
            local_start = start - chunk_char_start
            local_end = end - chunk_char_start
            quoted = chunk_text[local_start:local_end]
            corpus[chunk_id] = chunk_text
            claims.append(Claim(
                section="",
                claim_text=claim_text,
                quoted_span=quoted,
                source_chunk_id=chunk_id,
            ))
        else:
            claims.append(Claim(
                section="",
                claim_text=claim_text,
                quoted_span="",
                source_chunk_id="",
            ))
    return claims, corpus


def build_report(limit: int | None) -> dict:
    load_env()
    engine = get_engine()
    investigations = _fetch_complete_investigations(engine, limit)

    rows: list[dict] = []
    total_claims = 0
    total_grounded = 0
    total_grounded_hits = 0

    for inv in investigations:
        claims, corpus = _fetch_claims_and_corpus(engine, inv["id"])
        grounded_claims = [c for c in claims if c.source_chunk_id]
        grounded = len(grounded_claims)
        # precision_grounded should be ~1.0 — anything lower is a grounder offset bug.
        precision_all = citation_precision(claims, corpus) if claims else 0.0
        precision_grounded = (
            citation_precision(grounded_claims, corpus) if grounded_claims else 0.0
        )
        hits_this = int(round(precision_grounded * grounded))
        total_claims += len(claims)
        total_grounded += grounded
        total_grounded_hits += hits_this
        rows.append({
            "investigation_id": inv["id"],
            "company": inv["company"],
            "completed_at": inv["completed_at"].isoformat() if inv["completed_at"] else None,
            "claims": len(claims),
            "grounded": grounded,
            "grounding_rate": grounded / len(claims) if claims else 0.0,
            "precision_over_grounded": precision_grounded,
            "precision_over_all": precision_all,
        })

    aggregate = {
        "investigations": len(rows),
        "total_claims": total_claims,
        "total_grounded": total_grounded,
        "grounding_rate": total_grounded / total_claims if total_claims else 0.0,
        "precision_over_grounded": (
            total_grounded_hits / total_grounded if total_grounded else 0.0
        ),
        "precision_over_all": (
            total_grounded_hits / total_claims if total_claims else 0.0
        ),
    }
    return {"per_investigation": rows, "aggregate": aggregate}


def _print_table(report: dict) -> None:
    print(f"\nEval scorecard — {report['aggregate']['investigations']} investigation(s)\n")
    print(
        f"  {'Company':<28}  {'Claims':>6}  {'Grnd':>5}  {'Grnd %':>7}  {'Prec|Grnd':>9}  {'Prec|All':>9}"
    )
    print(f"  {'-'*28}  {'-'*6}  {'-'*5}  {'-'*7}  {'-'*9}  {'-'*9}")
    for r in report["per_investigation"]:
        print(
            f"  {(r['company'] or '')[:28]:<28}  "
            f"{r['claims']:>6}  "
            f"{r['grounded']:>5}  "
            f"{r['grounding_rate']*100:>6.1f}%  "
            f"{r['precision_over_grounded']*100:>8.1f}%  "
            f"{r['precision_over_all']*100:>8.1f}%"
        )
    a = report["aggregate"]
    print()
    print(f"  Aggregate: {a['total_claims']} claims across {a['investigations']} investigation(s)")
    print(f"    Grounding rate                 : {a['grounding_rate']*100:.1f}%")
    print(f"    Precision | grounded subset    : {a['precision_over_grounded']*100:.1f}%")
    print(f"    Precision | all claims (funnel): {a['precision_over_all']*100:.1f}%")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = build_report(args.limit)
    if args.json:
        json.dump(report, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        _print_table(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
