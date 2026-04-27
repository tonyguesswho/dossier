from __future__ import annotations
# Single-attempt fail-open: free-tier 429s burn quota, retries hurt more than help.

import logging
from datetime import datetime, timezone
from typing import Any

from dossier.core.settings import get_settings
from dossier.investigate.tools.types import ToolResult

logger = logging.getLogger(__name__)

_CRUNCHBASE_BASE = "https://api.crunchbase.com/api/v4"
_CRUNCHBASE_AUTOCOMPLETE = "https://autocomplete.crunchbase.com/v4/autocomplete"


async def _fetch_org_summary(
    company: str, api_key: str | None
) -> dict[str, Any] | None:
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
        logger.info("crunchbase: HTTP error for %r: %s", company, exc)
        return None


async def search(company: str) -> list[ToolResult]:
    api_key = get_settings().crunchbase_api_key or None

    try:
        org_data = await _fetch_org_summary(company, api_key)
    except Exception:  # noqa: BLE001 — fail-open
        logger.warning("crunchbase.search: unexpected error for %r", company, exc_info=True)
        return []

    if not org_data:
        logger.info("crunchbase.search: no org for %r", company)
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

    return [
        ToolResult(
            url=website,
            source_kind="crunchbase",
            text=text,
            title=f"{company} — Crunchbase",
            fetched_at=datetime.now(timezone.utc),
            raw_metadata=org_data,
        )
    ]

