from __future__ import annotations
# Falls back to unauthenticated (60 req/hr) when no PAT is set.

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
    token_secret = get_settings().github_token
    token = token_secret.get_secret_value().strip() if token_secret is not None else ""
    if strict and not token:
        raise RuntimeError("GITHUB_TOKEN not set")
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
    # Fail-open on no match / search error. Per-repo failures are logged + skipped.
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
            logger.info("GitHub user search returned 0 results for %r", founder_name)
            return []

        top = items[0]
        login = top.get("login", "")
        if not login:
            return []

        results: list[ToolResult] = []

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

