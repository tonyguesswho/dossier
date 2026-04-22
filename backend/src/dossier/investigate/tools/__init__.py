"""Tool wrappers for external data sources.

Phase 2 ships three (CONTEXT.md D-11):
  - exa.py      → web search (primary)
  - github.py   → founder repos/activity (INVEST-02)
  - firecrawl.py → deep-crawl seed URL (budget ≤1/investigation per D-13)

Deferred to Phase 3 (CONTEXT.md D-12):
  - news (NewsAPI / GDELT / Bing News fallback — ToS risk deserves own decision)
  - crunchbase free tier — added alongside NewsAPI when parallel fan-out lands
"""
