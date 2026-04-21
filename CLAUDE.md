# Dossier

Cited-OSINT AI investigation agent for seed-stage VC first-meeting prep. Paste a company name, URL, or pitch deck → 2–4 minutes later get a one-page cited brief + grounded chat. Every claim anchors to a verbatim source span; accuracy is proven by a held-out eval set.

**Bootcamp capstone. 14-day solo build. Started 2026-04-21.**

## Where things live

All planning docs are local-only (gitignored). Read these in order when resuming:

- `.planning/PROJECT.md` — project contract: core value, requirements summary, constraints, key decisions
- `.planning/REQUIREMENTS.md` — 39 v1 requirements across 8 categories with phase traceability
- `.planning/ROADMAP.md` — 7 phases, success criteria, bootcamp-surface coverage
- `.planning/STATE.md` — current phase pointer and progress
- `.planning/research/SUMMARY.md` — consolidated research; starts here before drilling into STACK/FEATURES/ARCHITECTURE/PITFALLS
- `.planning/research/STACK.md` — locked library choices with versions and rationale
- `.planning/research/FEATURES.md` — v1/v2/anti-feature catalog with priorities
- `.planning/research/ARCHITECTURE.md` — components, data flows, LangGraph schema, data model, build order
- `.planning/research/PITFALLS.md` — 55 pitfalls with prevention + phase mapping

## Locked decisions (don't re-litigate)

- **LLM gateway:** OpenRouter via stock `openai` Python SDK with `base_url="https://openrouter.ai/api/v1"`. Never wire a dedicated OpenRouter client.
- **Agent framework:** LangGraph 1.0 with `AsyncPostgresSaver` checkpointing. Never `MemorySaver` in deployed code.
- **Backend:** FastAPI + Mangum in Lambda **container images** (not zip) — `pdf2image` needs poppler at the OS level. Two-Lambda split: thin `api-lambda` (poll + chat), heavy `investigate-lambda` (async graph).
- **Storage:** S3 for raw sources + pitch decks; RDS Postgres t3.micro + pgvector 0.8 for investigations, source chunks, claims, chat, eval data. RDS Proxy is mandatory (not optional).
- **Frontend:** Next.js 16 App Router + React 19 + Tailwind 4 + Clerk on Vercel. Polling via TanStack Query. Chat streaming via `@microsoft/fetch-event-source`, not the Vercel AI SDK.
- **Observability:** Langfuse 3.x. On Lambda, always `flush() + shutdown() + sleep(15)` before handler returns.
- **Search:** Exa (not Tavily — Tavily hides source spans). GitHub API, Firecrawl, NewsAPI, Crunchbase free tier as secondary sources.
- **Data policy:** public sources only in v1. No LinkedIn. No paid providers.

## Ways of working

- **Explain decisions and tradeoffs always.** The demo panel grades on articulation, not just output. Name alternatives considered and why rejected.
- **Model profile = balanced (Sonnet).** Opus is too expensive; don't default to it.
- **Git commits: never add `Co-Authored-By: Claude` or any Anthropic trailer.** Commits must read as Anthony's own work.
- **Interactive mode on GSD workflows.** User wants a decision gate at each step.
- **`.planning/` is gitignored.** Planning docs do not commit to the project repo; `gsd-sdk query commit` honors this.

## Commands

- `gsd-sdk query <handler>` — GSD SDK query handlers
- `/gsd-discuss-phase 1` — next step from here (gathers context before phase-1 planning)
- `/gsd-plan-phase 1` — skip discussion and plan Phase 1 directly
- `/gsd-progress` — show current status

## Golden path from here

`/clear` then `/gsd-discuss-phase 1`. Phase 1 builds the eval harness and repo skeleton before any agent code lands.
