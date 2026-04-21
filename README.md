# Dossier

Cited-OSINT AI investigation agent for seed-stage VC first-meeting prep.
Capstone project — 14-day solo build.

See `.planning/PROJECT.md` for the full project contract.

## Quickstart (Phase 1)

**Prereqs:** Docker, Python 3.12+, pnpm (or npm), [uv](https://docs.astral.sh/uv/) recommended.

1. Copy `.env.example` to `.env` and fill in Langfuse keys (OpenRouter key not required for Phase 1).
2. Start local Postgres:
   ```bash
   docker-compose up -d postgres
   ```
3. Install backend deps and apply schema:
   ```bash
   cd backend
   uv sync
   uv run alembic upgrade head
   ```
4. Install frontend deps (skeleton only; no pages yet):
   ```bash
   cd frontend
   pnpm install
   ```
5. Run unit tests:
   ```bash
   cd backend
   uv run pytest tests/unit -q
   ```

## Repo layout

- `backend/` — Python package (FastAPI/LangGraph deferred to later phases)
- `frontend/` — Next.js 16 App Router skeleton (pages deferred to Phase 2)
- `infra/terraform/` — Terraform stubs (not applied in Phase 1)
- `docker-compose.yml` — local Postgres (pgvector/pgvector:pg17)
- `.planning/` — GSD planning docs (gitignored, local only)

## Locked decisions

See `CLAUDE.md`. Do not re-litigate:
OpenRouter via stock openai SDK · LangGraph 1.0 + AsyncPostgresSaver · Lambda container images · RDS Proxy mandatory · Next.js 16 + Clerk + @microsoft/fetch-event-source · Langfuse 3.x · Exa (not Tavily) · public data only.
