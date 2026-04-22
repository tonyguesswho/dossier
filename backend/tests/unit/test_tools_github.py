"""Unit tests for dossier.investigate.tools.github.

Uses respx to mock httpx — per STACK.md §2.11 + PATTERNS.md §"Apply to: tests that
mock out external HTTP (Exa/GitHub/Firecrawl)".

Locked by 02-CONTEXT.md D-11 (GitHub for INVEST-02) + STACK.md §2.4 (httpx direct).
Target runtime: <300ms.
"""
from __future__ import annotations

import httpx
import pytest
import respx

from dossier.investigate.tools import github as gh_module


@respx.mock
def test_fetch_founder_profile_returns_profile_and_repos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    respx.get("https://api.github.com/search/users", params={"q": "alice", "per_page": "3"}).mock(
        return_value=httpx.Response(200, json={"items": [{"login": "alice"}]})
    )
    respx.get("https://api.github.com/users/alice").mock(
        return_value=httpx.Response(
            200,
            json={
                "name": "Alice Example",
                "login": "alice",
                "html_url": "https://github.com/alice",
                "bio": "ML engineer",
                "company": "Acme AI",
                "public_repos": 42,
                "followers": 100,
            },
        )
    )
    respx.get(
        "https://api.github.com/users/alice/repos",
        params={"sort": "updated", "per_page": "5"},
    ).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "name": "acme-core",
                    "html_url": "https://github.com/alice/acme-core",
                    "description": "Core lib",
                    "stargazers_count": 30,
                    "language": "Python",
                },
                {
                    "name": "tools",
                    "html_url": "https://github.com/alice/tools",
                    "description": None,
                    "stargazers_count": 5,
                    "language": "Rust",
                },
            ],
        )
    )

    results = gh_module.fetch_founder_profile("alice")
    assert len(results) == 3
    assert results[0].source_kind == "github"
    assert results[0].url == "https://github.com/alice"
    assert "ML engineer" in results[0].text
    assert results[1].title == "acme-core"
    assert results[1].raw_metadata == {"stars": 30, "language": "Python"}


@respx.mock
def test_fail_open_on_no_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    respx.get("https://api.github.com/search/users").mock(
        return_value=httpx.Response(200, json={"items": []})
    )
    assert gh_module.fetch_founder_profile("noonehere") == []


@respx.mock
def test_fail_open_on_search_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    respx.get("https://api.github.com/search/users").mock(
        return_value=httpx.Response(503, json={"message": "upstream"})
    )
    # After 3 retries, wrapper returns [] (fail-open on search error)
    assert gh_module.fetch_founder_profile("alice") == []


def test_unauthed_headers_omit_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(gh_module, "load_env", lambda: None)
    headers = gh_module._headers(gh_module.read_github_env(strict=False))
    assert "Authorization" not in headers
    assert headers["Accept"] == "application/vnd.github+json"
