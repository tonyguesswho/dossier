"""Alembic environment for Dossier.

Reads DATABASE_URL from the environment (populated by python-dotenv from .env).
The URL format in .env.example is `postgresql+psycopg://dossier:dossier@localhost:5432/dossier`
which routes SQLAlchemy to the psycopg v3 driver — same driver used at runtime per STACK.md §2.3.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from dotenv import load_dotenv
from sqlalchemy import engine_from_config, pool

# Load .env from repo root (two levels up from backend/alembic/env.py).
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

database_url = os.environ.get("DATABASE_URL")
if not database_url:
    raise RuntimeError(
        "DATABASE_URL not set. Copy .env.example to .env and fill in local Postgres creds."
    )
config.set_main_option("sqlalchemy.url", database_url)

# Phase 1 has no ORM models — migrations are raw SQL via op.execute(). target_metadata stays None.
target_metadata = None


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
