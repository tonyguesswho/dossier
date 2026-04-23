"""Citation-precision scorecard over all completed investigations.

Runs `citation_precision()` from the Phase 1 scorer against the live DB.
Produces the numbers the demo slide quotes.

Usage (from backend/):
    uv run python -m dossier.eval.report          # all investigations
    uv run python -m dossier.eval.report --limit 10
    uv run python -m dossier.eval.report --json   # machine-readable output

Why this lives in src/ rather than scripts/: it's an evaluated artifact of
the project, not a one-off harness. A panelist can import it. Phase 4's
UI scorecard integration is a downstream caller.
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
    """Return complete investigations ordered by most recent completion."""
    # investigations has no `company_name` column — the user-facing name is
    # `input_ref` when input_type='name' (a URL otherwise). Good enough for
    # the scorecard's display column.
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
    """Return (claims with quoted_span derived from chunk slice, corpus: chunk_id → text).

    A claim's `quoted_span` is derived from `source_chunks.text[start:end]` when
    the grounder successfully pinned the claim. Ungrounded claims (NULL
    grounded_source_chunk_id) are returned with an empty `source_chunk_id` so
    citation_precision scores them 0 per D-10 case 5.
    """
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
            # claims.grounded_span_start/end are SOURCE-ABSOLUTE offsets (into
            # the full fetched source), while sc.text is the chunk's local
            # substring. Translate to chunk-local before slicing.
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
                source_chunk_id="",  # scores 0 per citation_precision
            ))
    return claims, corpus


def build_report(limit: int | None) -> dict:
    load_env()
    engine = get_engine()
    investigations = _fetch_complete_investigations(engine, limit)

    rows: list[dict] = []
    total_claims = 0
    total_grounded = 0
    total_grounded_hits = 0  # grounded claims whose quoted_span substrings into the chunk

    for inv in investigations:
        claims, corpus = _fetch_claims_and_corpus(engine, inv["id"])
        grounded_claims = [c for c in claims if c.source_chunk_id]
        grounded = len(grounded_claims)
        # Precision over ALL claims (ungrounded count as 0 — pessimistic, full-funnel view)
        precision_all = citation_precision(claims, corpus) if claims else 0.0
        # Precision over GROUNDED subset only — by construction should be ~1.0 if
        # grounder stores offsets correctly. Deviation points to grounder bugs.
        precision_grounded = (
            citation_precision(grounded_claims, corpus) if grounded_claims else 0.0
        )
        # Hit count for this investigation (grounded AND quoted_span in chunk)
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
