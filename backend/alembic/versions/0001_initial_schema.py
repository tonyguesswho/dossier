"""Create the initial Dossier schema.

Revision ID: 0001
Revises:
Create Date: 2026-04-21
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Extensions
    # ------------------------------------------------------------------
    # pgvector >= 0.8 provides vector type, vector_cosine_ops, and hnsw access method.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # pgcrypto provides gen_random_uuid(). PG17 does ship pgcrypto; the IF NOT EXISTS
    # guard makes this idempotent against managed Postgres variants.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # ------------------------------------------------------------------
    # ENUM types
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TYPE investigation_status AS ENUM (
            'queued','planning','gathering','synthesizing','grounding','complete','failed'
        )
        """
    )

    # ------------------------------------------------------------------
    # users — Clerk-managed; we only keep the id for FKs
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE users (
            id               TEXT PRIMARY KEY,
            created_at       TIMESTAMPTZ DEFAULT now()
        )
        """
    )

    # ------------------------------------------------------------------
    # eval_items — CREATED BEFORE investigations because investigations.eval_item_id FK.
    #
    # Deviations D-01/D-12/D-17 applied here:
    #   gold_brief_json JSONB   (replaces gold_brief_markdown TEXT)
    #   holdout BOOLEAN NOT NULL DEFAULT false   (new column)
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE eval_items (
            id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            company_name           TEXT NOT NULL,
            seed_url               TEXT,
            gold_brief_json        JSONB,
            holdout                BOOLEAN NOT NULL DEFAULT false,
            notes                  TEXT,
            active                 BOOLEAN DEFAULT true,
            created_at             TIMESTAMPTZ DEFAULT now()
        )
        """
    )

    # ------------------------------------------------------------------
    # investigations
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE investigations (
            id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id                    TEXT NOT NULL REFERENCES users(id),
            status                     investigation_status NOT NULL DEFAULT 'queued',
            input_type                 TEXT NOT NULL CHECK (input_type IN ('name','url','deck')),
            input_ref                  TEXT NOT NULL,
            started_at                 TIMESTAMPTZ DEFAULT now(),
            completed_at               TIMESTAMPTZ,
            brief_markdown             TEXT,
            citation_precision_score   REAL,
            langfuse_trace_id          TEXT,
            eval_item_id               UUID REFERENCES eval_items(id),
            error                      TEXT
        )
        """
    )
    op.execute("CREATE INDEX ON investigations (user_id, started_at DESC)")
    op.execute(
        "CREATE INDEX ON investigations (status) WHERE status NOT IN ('complete','failed')"
    )

    # ------------------------------------------------------------------
    # sources — one fetch = one row; many chunks per source.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE sources (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            investigation_id     UUID NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
            url                  TEXT NOT NULL,
            fetched_at           TIMESTAMPTZ DEFAULT now(),
            content_hash         TEXT NOT NULL,
            raw_text_s3_key      TEXT NOT NULL,
            source_kind          TEXT NOT NULL CHECK (source_kind IN
                                   ('web','github','news','crawl','deck_page','crunchbase')),
            metadata             JSONB DEFAULT '{}'::jsonb,
            UNIQUE (investigation_id, content_hash)
        )
        """
    )
    op.execute("CREATE INDEX ON sources (investigation_id)")

    # ------------------------------------------------------------------
    # source_chunks — includes the HNSW vector index.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE source_chunks (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_id     UUID NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            chunk_index   INT NOT NULL,
            text          TEXT NOT NULL,
            char_start    INT NOT NULL,
            char_end      INT NOT NULL,
            embedding     VECTOR(1536),
            metadata      JSONB DEFAULT '{}'::jsonb
        )
        """
    )
    op.execute("CREATE INDEX ON source_chunks (source_id)")
    # HNSW per STACK.md §2.3 — vector_cosine_ops matches text-embedding-3-small.
    # m=16, ef_construction=64 are the pgvector defaults but the spec prescribes them
    # explicitly so future pgvector minor-version default changes cannot silently alter
    # our index build characteristics.
    op.execute(
        """
        CREATE INDEX source_chunks_embedding_idx ON source_chunks
            USING hnsw (embedding vector_cosine_ops)
            WITH (m=16, ef_construction=64)
        """
    )

    # ------------------------------------------------------------------
    # claims — grounded_source_chunk_id is nullable so unsupported claims
    # can still be inserted and counted by the Phase 4 hallucination_rate metric.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE claims (
            id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            investigation_id           UUID NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
            section                    TEXT NOT NULL,
            claim_text                 TEXT NOT NULL,
            grounded_source_chunk_id   UUID REFERENCES source_chunks(id),
            grounded_span_start        INT,
            grounded_span_end          INT,
            confidence                 REAL,
            ordinal                    INT NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX ON claims (investigation_id, section, ordinal)")

    # ------------------------------------------------------------------
    # eval_runs — citation_precision + hallucination_rate are both NOT NULL.
    # Phase 1 creates the column; Phase 4 will populate it.
    # ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE eval_runs (
            id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            investigation_id       UUID NOT NULL REFERENCES investigations(id),
            eval_item_id           UUID NOT NULL REFERENCES eval_items(id),
            citation_precision     REAL NOT NULL,
            hallucination_rate     REAL NOT NULL,
            brief_similarity       REAL,
            model_id               TEXT NOT NULL,
            temperature            REAL NOT NULL,
            seed                   INT,
            ran_at                 TIMESTAMPTZ DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ON eval_runs (eval_item_id, ran_at DESC)")


def downgrade() -> None:
    # Reverse order of upgrade(); CASCADE handles FK-referenced rows.
    op.execute("DROP TABLE IF EXISTS eval_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS claims CASCADE")
    op.execute("DROP TABLE IF EXISTS source_chunks CASCADE")
    op.execute("DROP TABLE IF EXISTS sources CASCADE")
    op.execute("DROP TABLE IF EXISTS investigations CASCADE")
    op.execute("DROP TABLE IF EXISTS eval_items CASCADE")
    op.execute("DROP TABLE IF EXISTS users CASCADE")
    op.execute("DROP TYPE IF EXISTS investigation_status")
    # Do NOT drop the vector or pgcrypto extensions — they may be used by other DBs
    # on the same instance. Extensions survive downgrade.
