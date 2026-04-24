# Dossier

**Cited-OSINT investigation agent for seed-stage VC first-meeting prep.**
Paste a company name, URL, or pitch deck → 2-4 minutes later get a one-page
brief with six sections (Founders, Company, Market, Product, Risk Flags,
Suggested Questions) where **every claim links to a verbatim source span**.
After the brief loads, a grounded chat pane lets you ask follow-ups that
stay cited to the same corpus.

Live: **https://dossier-sage-omega.vercel.app** (backend on AWS Lambda,
frontend on Vercel).

---

## For reviewers — start here

If you are peer-reviewing Dossier, the fastest way to get oriented is this
10-minute tour:

1. **Read the core-value line above.** The project lives or dies on one
   property: *every factual claim has a citation, and the citations verify*.
   Everything else is in service of that.
2. **[DECISIONS.md](./DECISIONS.md)** — 15 architectural choices with
   rejected alternatives and rationale. This is where the heart of the
   project is documented; it's also the easiest place to form a specific,
   defensible critique.
3. **Eval numbers (see `Citation precision` section below).** The harness
   that scores the brief is `backend/src/dossier/eval/`. Run it:
   ```bash
   cd backend && uv run python -m dossier.eval.report
   ```
   Output is a table plus aggregate numbers — those are the demo scorecard.
4. **Five files to anchor code-level feedback on:**
   - [`backend/src/dossier/investigate/graph/graph.py`](./backend/src/dossier/investigate/graph/graph.py)
     — the LangGraph topology (planner → gather_fanout → founder_extraction
     → stage2_router → ingest_and_embed → verifier → synthesizer → finalize,
     with reflection cap)
   - [`backend/src/dossier/investigate/ground.py`](./backend/src/dossier/investigate/ground.py)
     — substring-matching grounder; the "citations don't lie" mechanism
   - [`backend/src/dossier/eval/scorer.py`](./backend/src/dossier/eval/scorer.py)
     — deterministic `citation_precision()` used by the eval harness
   - [`backend/src/dossier/investigate/graph/nodes/ingest_and_embed.py`](./backend/src/dossier/investigate/graph/nodes/ingest_and_embed.py)
     — chunking + embedding + the Haiku injection classifier (quarantines
     prompt-injection attempts per GUARD-02)
   - [`infra/terraform/`](./infra/terraform/) — single-stack AWS deploy
     (no VPC, no API Gateway, no RDS Proxy — see DECISIONS.md for why)
5. **Known limitations** are in the `Known Limitations` section below.
   These are open questions where specific peer feedback would land best.

---

## Architecture at a glance

```
┌──────────────────┐    HTTPS    ┌──────────────────────────┐    HTTPS    ┌──────────────┐
│  Next.js 16      │ ──────────▶ │ Vercel route handlers    │ ──────────▶ │ AWS Lambda   │
│  (Vercel)        │             │ /api/* (Clerk JWT mint)  │             │ (FastAPI via │
│  + Clerk auth    │ ◀────────── │                          │ ◀────────── │  Mangum)     │
└──────────────────┘   JSON+SSE  └──────────────────────────┘   JSON+SSE  └──────┬───────┘
                                                                                 │
                                                                                 ▼
                                                              ┌──────────────────────────────────────┐
                                                              │  lambda_handler: event-shape dispatch │
                                                              │                                        │
                                                              │   HTTP event → Mangum → FastAPI routes │
                                                              │                                        │
                                                              │   {investigation_id: uuid}             │
                                                              │   → runner.run_graph() → LangGraph     │
                                                              └──────────┬───────────────────────────┘
                                                                         │
                        ┌────────────────────────────────────────────────┴────────────────────────┐
                        │                              LangGraph                                  │
                        │  planner ─▶ gather_fanout ─▶ founder_extraction ─▶ stage2_router        │
                        │                                                         │               │
                        │                                                         ▼               │
                        │                  (ingest_and_embed) ◀── fan-in ── (per-founder GitHub)  │
                        │                          │                                              │
                        │                          ▼                                              │
                        │                      verifier ──(reflect, cap 2)──┐                     │
                        │                          │                        │                     │
                        │                          ▼                        ▼                     │
                        │                     synthesizer ───────▶ finalize (ground + persist)    │
                        └─────────────┬──────────────┬──────────────────┬──────────────────────────┘
                                      │              │                  │
                                      ▼              ▼                  ▼
                               ┌────────────┐ ┌────────────┐ ┌──────────────────┐
                               │ RDS        │ │ Langfuse   │ │ OpenRouter       │
                               │ Postgres + │ │ (per-node  │ │ (Sonnet / Haiku, │
                               │ pgvector   │ │  spans +   │ │  stock openai    │
                               │ + HNSW     │ │  traces)   │ │  SDK)            │
                               └────────────┘ └────────────┘ └──────────────────┘
                                                                      │
                                                                      └─▶ OpenAI (embeddings only;
                                                                          OpenRouter doesn't proxy
                                                                          /v1/embeddings — see
                                                                          DECISIONS.md #6)
```

**Tool layer (invoked inside `gather_fanout`):** Exa web search (3 angle-tagged
queries — name, founders, funding) · GitHub founder-profile search · Firecrawl
crawl (≤1 per investigation) · NewsAPI · Crunchbase. Every tool has fail-open
behavior so one dead source never kills the investigation.

---

## Citation precision (the headline metric)

A brief is worthless if the citations are fabricated. Dossier's grounder
(`ground.py`) refuses to emit a claim it can't verify against a verbatim
source substring, so `brief.claims` is implicitly a *filtered* set of the
synthesizer's output — claims without a verifiable quote get dropped rather
than cited with a hallucinated URL.

Scorecard over the 5 most recent investigations:

| Metric | Value |
|---|---|
| Total claims | 151 |
| Grounded claims | 81 |
| Grounding rate | 53.6% |
| **Precision (over grounded subset)** | **100.0%** |
| Precision (end-to-end funnel) | 53.6% |

> **How to read this:** the synthesizer proposes ~30 claims per investigation.
> The grounder accepts ~half. Of the accepted half, every single quoted span
> is byte-identical to a substring of the cited source chunk. "Honest gap" is
> our posture — rather than fabricate, we drop.

Run the scorecard yourself:

```bash
cd backend && uv run python -m dossier.eval.report           # pretty table
cd backend && uv run python -m dossier.eval.report --json    # machine-readable
```

Phase 4 would ship per-investigation scorecards inline in the UI. Demo-lite
keeps the CLI.

---

## Known Limitations

Open items where specific peer feedback would land best:

- **Two dispatch pipelines in-tree** (`pipeline.py` linear + graph via
  `runner.py`). Contract drift is a real risk (bit us once on deploy day
  when `finalize` didn't persist `brief_markdown`). Post-capstone: delete
  linear, unify on graph. See DECISIONS.md #12.
- **Chat widen-search pulls arbitrary Exa results.** When retrieval is
  weak (top-1 distance > 0.4), chat fires a fresh Exa query scoped to
  the investigation subject and ingests new chunks *permanently* into
  that investigation's corpus. A bad query can pollute the corpus; we
  tag widened chunks in metadata so they can be audited/pruned, but
  there's no automated cleanup.
- **Markdown extraction from Exa/Firecrawl is occasionally lossy.**
  Text artifacts like `$240million` (missing space) or `do not yd
  enough` (truncated word) slip through. No pre-ingest normalization
  layer exists.
- **Account-level Lambda public-access block is a deploy-time gotcha.**
  If you re-deploy to a fresh AWS account, the `/healthz` 403s until
  you toggle the account setting in the Lambda console. Not captured
  in Terraform (no CLI API at the time of this capstone).
- **MarkItDown PDF conversion is text-only.** Image-heavy pitch decks
  produce sparse briefs; no vision-model fallback in the demo build.
  Phase 5 full-scope (pdf2image + vision) is post-capstone.
- **RDS security group is `0.0.0.0/0` on port 5432.** Fine for demo,
  tighten to a specific IP range for any real traffic.

---

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
