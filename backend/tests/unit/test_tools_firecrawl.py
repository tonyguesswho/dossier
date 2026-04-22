"""Unit tests for dossier.investigate.tools.firecrawl.

Locked by 02-CONTEXT.md D-11 (Firecrawl included) + D-13 (≤1 crawl/investigation).
Mocks the FirecrawlApp class via monkeypatch; no real network calls.
Target runtime: <200ms.

Note on installed API (firecrawl-py 4.22.3):
  The v2 client exposes `Firecrawl.scrape(url, formats=[...])` — NOT the old
  `scrape_url(url, params={...})`. Our wrapper calls `.scrape(...)` because
  `firecrawl.FirecrawlApp` is aliased to the v2 `Firecrawl` class in this version.
"""
from __future__ import annotations

from typing import Any

import pytest

from dossier.investigate.tools import firecrawl as fc_module


class _FakeMetadata:
    def __init__(self, title: str | None) -> None:
        self.title = title


class _FakeDocument:
    """Mirrors the shape of firecrawl.v2.types.Document (markdown + metadata attrs)."""

    def __init__(self, markdown: str, title: str | None = None) -> None:
        self.markdown = markdown
        self.metadata = _FakeMetadata(title) if title is not None else None


class _FakeFirecrawlApp:
    def __init__(self, api_key: str) -> None:  # noqa: ARG002
        self.api_key = api_key

    def scrape(self, url: str, **_kwargs: Any) -> _FakeDocument:  # noqa: ARG002
        return _FakeDocument(
            markdown=f"# Page for {url}\nFounded in 2024.",
            title="Acme AI",
        )


def _install_fake_firecrawl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test-fake")
    import firecrawl as firecrawl_pkg
    monkeypatch.setattr(firecrawl_pkg, "FirecrawlApp", _FakeFirecrawlApp)
    fc_module.reset_budget_for_tests()


def test_crawl_returns_tool_result(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_firecrawl(monkeypatch)
    results = fc_module.crawl_seed_url("https://acme.ai", investigation_id="inv-1")
    assert len(results) == 1
    assert results[0].url == "https://acme.ai"
    assert results[0].source_kind == "crawl"
    assert "Founded in 2024" in results[0].text
    assert results[0].title == "Acme AI"


def test_budget_enforced_after_first_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_firecrawl(monkeypatch)
    tracker: dict[str, int] = {}
    first = fc_module.crawl_seed_url(
        "https://acme.ai", investigation_id="inv-1", _budget_tracker=tracker
    )
    second = fc_module.crawl_seed_url(
        "https://acme.ai/about", investigation_id="inv-1", _budget_tracker=tracker
    )
    assert len(first) == 1
    assert second == []  # budget exhausted
    assert tracker["inv-1"] == 1


def test_rejects_non_http_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_firecrawl(monkeypatch)
    with pytest.raises(ValueError, match="non-http"):
        fc_module.crawl_seed_url("ftp://x/y", investigation_id="inv-1")


def test_fail_open_on_empty_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    class _EmptyFirecrawl:
        def __init__(self, api_key: str) -> None:  # noqa: ARG002
            pass

        def scrape(self, url: str, **_kwargs: Any) -> _FakeDocument:  # noqa: ARG002
            return _FakeDocument(markdown="", title=None)

    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test-fake")
    import firecrawl as firecrawl_pkg
    monkeypatch.setattr(firecrawl_pkg, "FirecrawlApp", _EmptyFirecrawl)
    fc_module.reset_budget_for_tests()
    assert fc_module.crawl_seed_url("https://empty.example", investigation_id="inv-1") == []
