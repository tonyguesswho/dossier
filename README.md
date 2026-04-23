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

## Deployment (minimal AWS)

The infra lives in `infra/terraform/` (single flat stack: one ECR repo, one
RDS Postgres, one IAM role, one Lambda container + Function URL — no VPC,
no RDS Proxy, no API Gateway, no S3). All AWS wiring is driven by the repo-root
`Makefile`.

### First-time deploy

```bash
# 1. Fill in secrets
cp infra/terraform/terraform.tfvars.example infra/terraform/terraform.tfvars
# edit terraform.tfvars — env_vars map needs the 11 backend keys
# (OPENROUTER_API_KEY, OPENAI_API_KEY, LANGFUSE_*, CLERK_JWKS_URL,
#  EXA_API_KEY, GITHUB_TOKEN, FIRECRAWL_API_KEY, NEWSAPI_API_KEY,
#  CRUNCHBASE_API_KEY) + DOSSIER_DISPATCH_MODE=lambda.

# 2. Initialize Terraform, bootstrap ECR (must exist before `docker push`)
cd infra/terraform && terraform init
terraform apply -target=aws_ecr_repository.backend

# 3. Build, push, apply everything else
cd ../..    # back to repo root
make push   # docker build --platform linux/amd64 + aws ecr push
make deploy # push (re-runs) + terraform apply with the real image URI

# 4. Apply DB schema once RDS is reachable
make migrate   # alembic upgrade head, DATABASE_URL pulled from terraform output

# 5. Point the frontend at the live backend
cd infra/terraform && terraform output lambda_function_url
#   -> https://<id>.lambda-url.us-east-1.on.aws/
# Set that URL as NEXT_PUBLIC_BACKEND_URL in Vercel project env and redeploy.
```

### Subsequent deploys

```bash
make deploy        # rebuild + push + terraform apply
make migrate       # only when schema changes (alembic revision added)
make logs          # tail /aws/lambda/<func>/... live
```

### Tear down

```bash
make destroy       # terraform destroy (drops RDS data; ECR images stay unless
                   # lifecycle policy evicts them)
```

### Makefile target index

```bash
make help          # self-documenting target list
```

Tradeoffs: a single Lambda container serves both the FastAPI surface and the
LangGraph investigate path — dispatch is by event shape in `backend/lambda_handler.py`.
The proper two-Lambda split (api-lambda 30s / investigate-lambda 900s) is a
post-capstone refactor; the seam is already cut so the split is additive.
