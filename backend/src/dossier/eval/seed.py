"""Seed eval_items from EVAL_COMPANIES + golds/*.json. Idempotent.

    cd backend && uv run python -m dossier.eval.seed
"""
from __future__ import annotations

import json
import logging
import pathlib
import sys

from pydantic import ValidationError
from sqlalchemy import create_engine, text

from dossier.eval.companies import EVAL_COMPANIES, EvalCompany, split_summary
from dossier.models import GoldClaim
from dossier.core.settings import Settings, get_settings

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# parents: [0]=eval, [1]=dossier, [2]=src, [3]=backend
_GOLDS_DIR = pathlib.Path(__file__).resolve().parents[3] / "eval" / "golds"


def _load_gold_claims(filename: str) -> list[GoldClaim]:
    path = _GOLDS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Gold brief missing: {path}. Author the gold or remove the "
            f"gold_filename= from EVAL_COMPANIES for this entry."
        )
    data = json.loads(path.read_text())
    raw_claims = data.get("claims", [])
    if not raw_claims:
        raise ValueError(f"Gold brief {path} has no `claims`.")
    # Reject the stub's placeholder content.
    for i, claim in enumerate(raw_claims):
        if claim.get("claim_text", "").startswith("Replace"):
            raise ValueError(
                f"Gold brief {path} claim #{i} is still a stub — author real claims first."
            )
    return [GoldClaim(**claim) for claim in raw_claims]


def _gold_json_payload(company: EvalCompany) -> str | None:
    if company.gold_filename is None:
        return None
    claims = _load_gold_claims(company.gold_filename)
    return json.dumps([c.model_dump() for c in claims])


def upsert_eval_items(database_url: str) -> dict[str, int]:
    # Owns the company_name UNIQUE constraint here — it's seed-script concern, not a migration.
    engine = create_engine(database_url, future=True)
    stats = {"inserted": 0, "updated": 0, "total_rows": 0}

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
    try:
        database_url = get_settings().database_url
    except Exception:  # noqa: BLE001
        print(
            "ERROR: DATABASE_URL not set. Copy .env.example to .env and fill local creds.",
            file=sys.stderr,
        )
        return 1

    summary = split_summary()
    logger.info(
        "Seeding eval_items: total=%d, train=%d, holdout=%d, golded=%d, verticals=%d",
        summary["total"], summary["train"], summary["holdout"],
        summary["golded"], summary["verticals"],
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
        stats["inserted"], stats["updated"], stats["total_rows"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
