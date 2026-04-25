"""Crunchbase free-tier enrichment wrapper for Phase 3 Stage-2 fan-out (D-04).

Crunchbase provides basic company metadata (description, funding, employees,
founded date) via their autocomplete + organization entity endpoints. Treat
as optional enrichment — if the free tier rate-limits or the API key is
missing, fail-open (return []) rather than crash the investigation.

Implementation notes:
  - Uses httpx (transitive dep via openai-python / firecrawl-py). Deferred
    import inside the fetch function so unit tests can monkeypatch
    httpx.AsyncClient without reloading this module (mirrors exa.py pattern).
  - No tenacity retry here — Crunchbase free tier's aggressive rate-limiting
    makes retries counterproductive (each 429 burns quota). One attempt per
    investigation; on failure log + fall through to empty result.
  - CRUNCHBASE_API_KEY is optional. Unauthenticated calls work at low QPS
    against the public autocomplete endpoint; an API key upgrades to the
    authenticated rate bucket.

Rate limits (free tier): aggressive and undocumented. Phase 3 budget: one
query per investigation (autocomplete → org lookup = 2 HTTP calls total).

Fail-open policy: any error (network, HTTP 4xx/5xx, empty response, missing
permalink) → log + return []. Matches github.py's two-stage fail-open: the
search step fails open entirely, downstream enrichment failures short-circuit
to whatever partial data was collected.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from dossier.core.settings import get_settings
from dossier.investigate.tools.types import ToolResult

logger = logging.getLogger(__name__)

_CRUNCHBASE_BASE = "https://api.crunchbase.com/api/v4"
_CRUNCHBASE_AUTOCOMPLETE = "https://autocomplete.crunchbase.com/v4/autocomplete"
_CB_USER_KEY_ENV = "CRUNCHBASE_API_KEY"  # optional; unauthenticated works at low QPS


async def _fetch_org_summary(
    company: str, api_key: str | None
) -> dict[str, Any] | None:
    """Two-step Crunchbase lookup: autocomplete → org entity properties.

    Returns the raw properties dict or None on any failure (missing permalink,
    HTTP error, empty response).
    """
    import httpx  # noqa: PLC0415 — deferred for monkeypatch safety

    ac_params: dict[str, Any] = {
        "query": company,
        "collection_ids": "organizations",
        "limit": 1,
    }
    if api_key:
        ac_params["user_key"] = api_key

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            ac_r = await client.get(_CRUNCHBASE_AUTOCOMPLETE, params=ac_params)
            ac_r.raise_for_status()
            entities = ac_r.json().get("entities", []) or []
            if not entities:
                return None
            identifier = entities[0].get("identifier") or {}
            permalink = identifier.get("permalink")
            if not permalink:
                return None

            org_params: dict[str, Any] = {
                "field_ids": (
                    "short_description,location_identifiers,num_employees_enum,"
                    "funding_total,founded_on,website_url,categories"
                ),
            }
            if api_key:
                org_params["user_key"] = api_key

            org_r = await client.get(
                f"{_CRUNCHBASE_BASE}/entities/organizations/{permalink}",
                params=org_params,
            )
            org_r.raise_for_status()
            return org_r.json().get("properties") or {}
    except httpx.HTTPError as exc:
        logger.info("crunchbase._fetch_org_summary: HTTP error for %r: %s", company, exc)
        return None


async def search(company: str) -> list[ToolResult]:
    """Fetch basic Crunchbase org profile for `company`.

    Returns a single-element list[ToolResult] with source_kind='crunchbase',
    or [] on any failure. Fail-open is mandatory here: Crunchbase is optional
    enrichment (T-03-04-04), not a required source.
    """
    api_key = get_settings().crunchbase_api_key or None

    try:
        org_data = await _fetch_org_summary(company, api_key)
    except Exception:  # noqa: BLE001 — fail-open on any unexpected error
        logger.warning(
            "crunchbase.search: unexpected error for company=%r", company, exc_info=True
        )
        return []

    if not org_data:
        logger.info("crunchbase.search: no org found for company=%r", company)
        return []

    description = org_data.get("short_description") or ""
    website = (
        org_data.get("website_url")
        or f"https://www.crunchbase.com/organization/"
        + company.lower().replace(" ", "-")
    )

    text_parts: list[str] = []
    if description:
        text_parts.append(description)

    funding = org_data.get("funding_total") or {}
    if isinstance(funding, dict):
        value = funding.get("value_usd")
        if value:
            try:
                text_parts.append(f"Total funding: ${float(value):,.0f} USD")
            except (TypeError, ValueError):
                pass

    employees = org_data.get("num_employees_enum")
    if employees:
        text_parts.append(f"Employees: {employees}")

    founded = org_data.get("founded_on") or {}
    if isinstance(founded, dict):
        founded_val = founded.get("value")
        if founded_val:
            text_parts.append(f"Founded: {founded_val}")

    text = "\n".join(text_parts)
    if not text.strip():
        logger.info("crunchbase.search: org found but all fields empty for %r", company)
        return []

    result = ToolResult(
        url=website,
        source_kind="crunchbase",
        text=text,
        title=f"{company} — Crunchbase",
        fetched_at=datetime.now(timezone.utc),
        raw_metadata=org_data,
    )
    logger.info("crunchbase.search: found org data for company=%r", company)
    return [result]


__all__ = ["search"]
