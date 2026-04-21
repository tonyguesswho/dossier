"""Load the 10-company eval set into the `eval_items` table.

This script is idempotent: running it twice changes no rows (the primary key is the
company_name, which is unique — re-seeds replace gold_brief_json and holdout via UPSERT).

Usage:
    cd backend
    # Make sure docker-compose postgres is running and migration 0001 applied.
    uv run python -m dossier.eval.seed

The script reads `EVAL_COMPANIES` from `dossier.eval.companies` and gold briefs from
`backend/eval/golds/*.json`. Companies flagged `gold_filename=...` have their gold
loaded and stored as `eval_items.gold_brief_json` (JSONB). Companies without a
`gold_filename` (holdout + gold headroom) get `gold_brief_json = NULL`.

Each gold file goes through `GoldClaim` Pydantic validation before the UPSERT —
if the shape is wrong (missing source_text, invalid section literal, etc.), the
seed script exits non-zero and no rows are written.

The `_meta` block in each gold JSON (authoring instructions) is filtered out —
only the `claims` array is persisted.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import sys

from pydantic import ValidationError
from sqlalchemy import create_engine, text

from dossier.eval.companies import EVAL_COMPANIES, EvalCompany, split_summary
from dossier.models import GoldClaim
from dossier.observability import load_env

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Resolve golds/ dir from this file: backend/src/dossier/eval/seed.py
# parents: [0]=eval, [1]=dossier, [2]=src, [3]=backend
_GOLDS_DIR = pathlib.Path(__file__).resolve().parents[3] / "eval" / "golds"


def _load_gold_claims(filename: str) -> list[GoldClaim]:
    """Read a gold JSON file and validate it into GoldClaim objects.

    Filters out the `_meta` authoring instructions block.
    Raises ValidationError if any claim violates the D-02 contract.
    """
    path = _GOLDS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Gold brief missing: {path}. Author the gold or remove the "
            f"gold_filename= from EVAL_COMPANIES for this entry."
        )
    data = json.loads(path.read_text())
    raw_claims = data.get("claims", [])
    if not raw_claims:
        raise ValueError(
            f"Gold brief {path} has no `claims` — authoring incomplete (see _meta.instructions)."
        )
    # Reject placeholder content — the stub's example claim_text starts with "Replace with".
    for i, claim in enumerate(raw_claims):
        if claim.get("claim_text", "").startswith("Replace"):
            raise ValueError(
                f"Gold brief {path} claim #{i} is still a stub (claim_text starts with 'Replace'). "
                f"Author real claims before seeding."
            )
    return [GoldClaim(**claim) for claim in raw_claims]


def _gold_json_payload(company: EvalCompany) -> str | None:
    """Return the JSONB payload for eval_items.gold_brief_json, or None for ungolded companies."""
    if company.gold_filename is None:
        return None
    claims = _load_gold_claims(company.gold_filename)
    # Store as list[dict] so the JSONB column is a clean array of GoldClaim objects.
    return json.dumps([c.model_dump() for c in claims])


def upsert_eval_items(database_url: str) -> dict[str, int]:
    """Upsert all EVAL_COMPANIES into the eval_items table. Idempotent.

    Uniqueness is on (company_name) — we use ON CONFLICT against that column.
    """
    engine = create_engine(database_url, future=True)
    stats = {"inserted": 0, "updated": 0, "total_rows": 0}

    # Add a unique constraint on company_name if it doesn't already exist.
    # This is idempotent via DO $$ ... EXCEPTION block pattern.
    # (Migration 0001 doesn't add this — seed.py owns it because it's a seed-script concern.)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'eval_items_company_name_unique'
                    ) THEN
                        ALTER TABLE eval_items
                            ADD CONSTRAINT eval_items_company_name_unique
                            UNIQUE (company_name);
                    END IF;
                END $$;
                """
            )
        )

        for company in EVAL_COMPANIES:
            gold_payload = _gold_json_payload(company)
            result = conn.execute(
                text(
                    """
                    INSERT INTO eval_items
                        (company_name, seed_url, gold_brief_json, holdout, notes, active)
                    VALUES
                        (:company_name, :seed_url, CAST(:gold_brief_json AS JSONB),
                         :holdout, :notes, true)
                    ON CONFLICT (company_name) DO UPDATE SET
                        seed_url = EXCLUDED.seed_url,
                        gold_brief_json = EXCLUDED.gold_brief_json,
                        holdout = EXCLUDED.holdout,
                        notes = EXCLUDED.notes,
                        active = EXCLUDED.active
                    RETURNING xmax = 0 AS inserted
                    """
                ),
                {
                    "company_name": company.company_name,
                    "seed_url": company.seed_url,
                    "gold_brief_json": gold_payload,
                    "holdout": company.holdout,
                    "notes": company.notes,
                },
            )
            row = result.fetchone()
            if row is not None and row.inserted:
                stats["inserted"] += 1
            else:
                stats["updated"] += 1

        total = conn.execute(text("SELECT COUNT(*) FROM eval_items")).scalar_one()
        stats["total_rows"] = total

    return stats


def main() -> int:
    load_env()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print(
            "ERROR: DATABASE_URL not set. Copy .env.example to .env and fill local creds.",
            file=sys.stderr,
        )
        return 1

    summary = split_summary()
    logger.info(
        "Seeding eval_items: total=%d, train=%d, holdout=%d, golded=%d, verticals=%d",
        summary["total"],
        summary["train"],
        summary["holdout"],
        summary["golded"],
        summary["verticals"],
    )

    try:
        stats = upsert_eval_items(database_url)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: gold brief validation failed: {exc}", file=sys.stderr)
        return 2
    except ValidationError as exc:
        print(f"ERROR: gold brief does not match GoldClaim schema: {exc}", file=sys.stderr)
        return 2

    logger.info(
        "Seed complete: inserted=%d updated=%d total_rows_in_table=%d",
        stats["inserted"],
        stats["updated"],
        stats["total_rows"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
