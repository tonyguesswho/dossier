"""GitHub REST wrapper — founder repos/activity per INVEST-02.

INVEST-02 is explicit: "agent synthesizes founder background from public sources
(personal sites, GitHub, conference talks, podcast appearances) — no LinkedIn."
GitHub covers the technical-founder surface area well; Exa covers non-technical.

Phase 2 flow: pipeline.py calls this once per founder name resolved from Exa's
initial company-name search. Parallel fan-out arrives in Phase 3 (D-04).

Rejected alternatives:
  - PyGithub / gh-sdk-python: STACK.md §2.4 locks httpx direct. PyGithub adds a
    dependency + opinionated pagination. For 3 calls per investigation, direct
    httpx is lighter.
  - Unauthenticated calls only: 60 req/hr is too tight for the demo. Accept a
    PAT in .env.
  - Search-only (no repo walk): loses the "ex-Google ML engineer" fact-check
    angle (INVEST-08 previews this in Phase 3).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from dossier.investigate.tools.types import ToolResult
from dossier.core.settings import get_settings

logger = logging.getLogger(__name__)

GITHUB_API_BASE: str = "https://api.github.com"
GITHUB_TIMEOUT_S: float = 15.0


def read_github_env(strict: bool = False) -> str:
    """Thin wrapper over Settings.github_token for backward compat.

    Returns empty string when the token isn't configured — _headers() then
    omits Authorization and we fall back to unauthenticated GitHub at
    60 req/hr (still useful for low-volume founder searches).
    """
    token_secret = get_settings().github_token
    token = token_secret.get_secret_value().strip() if token_secret is not None else ""
    if strict and not token:
        raise RuntimeError(
            "GITHUB_TOKEN not set. Generate a PAT at https://github.com/settings/tokens "
            "(scopes: public_repo, read:user) and paste into .env."
        )
    return token


def _headers(token: str) -> dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "dossier-investigate/0.1",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    retry=retry_if_exception_type(httpx.HTTPError),
    reraise=True,
)
def _get_json(client: httpx.Client, path: str, params: dict[str, Any] | None = None) -> Any:
    response = client.get(path, params=params or {})
    response.raise_for_status()
    return response.json()


def fetch_founder_profile(founder_name: str, *, token: str | None = None) -> list[ToolResult]:
    """Search GitHub for the founder and return up to ~6 ToolResults (1 profile + ~5 repos).

    Fail-open: returns [] if no user matches OR if the search endpoint errors
    after 3 retries. Individual profile/repo fetch failures after the search
    succeeds are logged and skipped, not fatal.
    """
    tok = token if token is not None else read_github_env(strict=False)
    with httpx.Client(
        base_url=GITHUB_API_BASE,
        headers=_headers(tok),
        timeout=GITHUB_TIMEOUT_S,
    ) as client:
        try:
            search_json = _get_json(
                client, "/search/users", params={"q": founder_name, "per_page": 3}
            )
        except httpx.HTTPError as exc:
            logger.warning("GitHub user search failed for %r: %s", founder_name, exc)
            return []

        items = search_json.get("items", []) if isinstance(search_json, dict) else []
        if not items:
            logger.info(
                "GitHub user search returned 0 results for %r (fail-open)", founder_name
            )
            return []

        top = items[0]
        login = top.get("login", "")
        if not login:
            return []

        results: list[ToolResult] = []

        # 1. Profile page
        try:
            profile = _get_json(client, f"/users/{login}")
            bio = (profile.get("bio") or "").strip()
            company = (profile.get("company") or "").strip()
            profile_text = (
                f"GitHub profile for {profile.get('name') or login}.\n"
                f"Bio: {bio}\nCompany: {company}"
            )
            results.append(
                ToolResult(
                    url=profile.get("html_url", f"https://github.com/{login}"),
                    source_kind="github",
                    text=profile_text,
                    title=f"GitHub · {login}",
                    raw_metadata={
                        "public_repos": profile.get("public_repos"),
                        "followers": profile.get("followers"),
                    },
                )
            )
        except httpx.HTTPError as exc:
            logger.warning("GitHub profile fetch failed for %r: %s", login, exc)

        # 2. Top 5 recently-updated repos
        try:
            repos = _get_json(
                client, f"/users/{login}/repos", params={"sort": "updated", "per_page": 5}
            )
        except httpx.HTTPError as exc:
            logger.warning("GitHub repos fetch failed for %r: %s", login, exc)
            repos = []

        for repo in repos:
            name = repo.get("name", "")
            desc = repo.get("description") or ""
            text = f"Repo: {name}\n{desc}"
            results.append(
                ToolResult(
                    url=repo.get("html_url", ""),
                    source_kind="github",
                    text=text,
                    title=name,
                    raw_metadata={
                        "stars": repo.get("stargazers_count"),
                        "language": repo.get("language"),
                    },
                )
            )

        return [r for r in results if r.url]


__all__ = ["GITHUB_API_BASE", "fetch_founder_profile", "read_github_env"]
