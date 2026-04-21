# Dossier

## What This Is

Dossier is a **first-meeting prep agent for seed-stage VCs**. A VC pastes a company name, URL, or pitch deck; 2–4 minutes later Dossier returns a one-page cited brief — founders, company, market, product, risk flags, and suggested questions — plus a grounded chat mode for digging deeper. Every claim in the brief links to the source span it came from, and citation accuracy is backed by a held-out eval set.

This is the capstone for an AI Engineering bootcamp. The product is designed to exercise a wide slice of the bootcamp surface area (RAG, multi-agent orchestration, tool use, MCP, evals, multi-modal, full-stack UI, AWS deployment, observability) in a use case that is real rather than contrived.

## Core Value

**Every claim in the brief is traceable to a cited source span, and that accuracy is provable via a held-out eval set.** Verifiable citations are what separate Dossier from general-purpose research assistants. If everything else fails, this must work.

## Requirements

### Validated

<!-- Shipped and confirmed valuable. -->

(None yet — ship to validate)

### Active

<!-- Current scope. Building toward these. Hypotheses until shipped. -->

**Investigation**
- [ ] User can trigger an investigation from a company name or URL
- [ ] User can upload a pitch deck PDF; Dossier extracts text and slide images and uses them to seed the investigation
- [ ] Investigation runs asynchronously in 2–4 min and reports progress to the UI
- [ ] Investigation returns a one-page brief with sections: Founders, Company, Market, Product, Risk Flags, Suggested Questions
- [ ] Every claim in the brief links to a source span (page + quoted text)
- [ ] Investigation persists its retrieved source corpus for reuse by chat

**Grounded chat**
- [ ] After an investigation completes, user can chat with it
- [ ] Chat answers are grounded in the investigation's source corpus and still cite
- [ ] Chat can dig into a claim ("why is the technical co-founder strong?")
- [ ] Chat can summarize any cited source
- [ ] Chat can widen the search ("find their three closest competitors' pricing") — triggers a targeted re-search, adds results to the corpus, answers with citations

**Evals**
- [ ] Hand-curated eval set of ~20 seed-stage companies built before agent implementation
- [ ] ~5–8 of those have hand-written gold briefs for end-to-end grading
- [ ] Citation-precision metric: % of quoted spans that actually appear in the linked source
- [ ] Hallucination rate: % of claims with no supporting retrieved evidence
- [ ] Eval results visible in the UI (per-investigation scorecard)

**Platform**
- [ ] Users authenticate via Clerk
- [ ] Investigations are scoped per user (multi-tenant)
- [ ] Langfuse traces every investigation and chat turn
- [ ] System deploys to AWS (S3, Lambda, RDS+pgvector) with Next.js frontend on Vercel

### Out of Scope

<!-- Explicit boundaries. Includes reasoning to prevent re-adding. -->

- **LinkedIn data** — no official API and scraping violates ToS. Deferred until a compliant path exists.
- **Series A/B and later-stage research** — relies on paywalled data (PitchBook, Crunchbase Pro). Would require budget and different retrieval strategy.
- **IC memo drafting** — long-form investment memos are too heavyweight for a 14-day solo build and do not demo well.
- **Deal-sourcing scan** — scanning *many* companies against a thesis is a different product (ranking pipeline, not research agent).
- **Cross-investigation memory / chat across dossiers** — each dossier is its own scoped context in v1. Avoids complex identity/graph reasoning.
- **Chat-initiated investigations** — investigations start only from the form. Keeps chat scope bounded.
- **Paid data providers** (PitchBook, Crunchbase Pro, Tracxn) — v1 is public-data-only to stay on free tiers.
- **Fine-tuning a custom model** — optional stretch; not required for the core value prop.
- **Open-ended web chat** — chat stays inside the investigation's corpus. No general-purpose browsing.

## Context

**Bootcamp surface area this is designed to exercise:**
- *Core LLM* — prompt engineering, embeddings, RAG, vector DBs (pgvector), evals, multi-modal
- *Agents* — LangGraph (stateful investigation graph), tool use, MCP integration
- *Full-stack* — Next.js 16 + React 19 + Tailwind 4 + Clerk (bootcamp's `saas/` and `twin/` patterns)
- *Deployment* — AWS (S3, Lambda, RDS), Langfuse observability, infra as code

**Bootcamp materials available** in the parent `/ai/` folder (`agents/`, `llm_engineering/`, `production/`, `alex/`, `twin/`, `saas/`, `instant/`) — these are reference implementations that can be mined for patterns.

**Why seed-stage specifically:**
- Companies at this stage live on public data (GitHub, company sites, press, HN, Twitter). Series A/B relies more on paywalled sources.
- Founder quality dominates the thesis — synthesizing a founder's background across public sources is exactly what LLM agents are good at.
- Meeting volume is high → pain is real → the "why this exists" narrative is legible.

**Why citation rigor:**
- Perplexity and ChatGPT-with-browsing routinely hallucinate quotes. A capstone that *measurably* gets citations right — with a held-out eval set and published scores — is interview-gold.
- Citation precision is an *objective* metric, unlike "brief quality," which is subjective. Objective metrics make the capstone defensible.

## Constraints

- **Timeline:** 14 days, solo build
- **Budget:** minimal — prefer free tiers; OpenRouter pay-as-you-go; AWS free tier where possible
- **Data:** public sources only in v1 (no paid providers, no LinkedIn, no scraping behind auth walls)
- **Tech stack:** align with bootcamp stack where it helps; substitute where it accelerates delivery
- **Compatibility:** AWS-first for infra, with documented fallback to Vercel/Modal if a specific AWS piece gets painful
- **Performance:** investigation ≤ 4 min end-to-end under normal conditions
- **Observability:** every investigation and every chat turn must be traceable in Langfuse

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Narrow to seed-stage VC first-meeting prep | 14-day scope; public-data tractable; high-volume pain point; clean demo | — Pending |
| LLM gateway = OpenRouter (not Bedrock) | Model flexibility (Claude / GPT / Gemini) and cross-model benchmarking as a capstone angle | — Pending |
| AWS for infra (S3, Lambda, RDS+pgvector) | Strong deployment story; aligns with `production/` bootcamp module | — Pending |
| Include pitch-deck upload in v1 | Covers the multi-modal bootcamp surface; VCs want it; PDF+vision is not a rabbit hole | — Pending |
| Public data only in v1 | Keeps costs near zero and bounds scope; no LinkedIn scraping | — Pending |
| LangGraph for the investigation graph | Stateful multi-step workflow with reflection/citation loop is a natural fit | — Pending |
| Build eval set before agent (Day 1–2) | Prevents overfitting and ensures the "wow" metric is honest | — Pending |
| Chat scoped to the investigation's corpus | Keeps scope tight; clean architecture (one retrieval layer, two consumers) | — Pending |
| Progress reporting via DB + polling (not WebSocket) | Ships faster; acceptable UX for 2–4 min tasks | — Pending |
| Next.js frontend on Vercel; backend on AWS | Plays to each platform's strength; frontend velocity + infra story | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-04-21 after initialization*
