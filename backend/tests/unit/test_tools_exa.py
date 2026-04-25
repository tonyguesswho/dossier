"""Unit tests for dossier.investigate.tools.exa.

Locked by .planning/phases/02-single-pass-rag-pipeline/02-CONTEXT.md:
  D-11: Exa is the primary web search tool in Phase 2.
  D-09 §Claude's Discretion: fail-open on Exa empties.

Tests mock the exa-py client via monkeypatch; no real network calls.
Target runtime: <200ms.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from dossier.investigate.tools import exa as exa_module


@dataclass
class _FakeExaResult:
    url: str
    text: str
    title: str | None = None
    score: float | None = None


class _FakeExaResponse:
    def __init__(self, results: list[_FakeExaResult]) -> None:
        self.results = results


class _FakeExaClient:
    def __init__(self, api_key: str) -> None:  # noqa: ARG002
        self.api_key = api_key

    def search_and_contents(self, query: str, **_kwargs) -> _FakeExaResponse:  # noqa: ARG002
        return _FakeExaResponse(
            [
                _FakeExaResult(
                    url="https://acme.ai",
                    text="Acme AI builds tools.",
                    title="Acme",
                    score=0.9,
                ),
                _FakeExaResult(
                    url="https://about.acme.ai",
                    text="Acme was founded in 2024.",
                    title="About",
                    score=0.8,
                ),
            ]
        )


def _install_fake_exa(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "ex-test-fake")
    import exa_py
    monkeypatch.setattr(exa_py, "Exa", _FakeExaClient)


def test_search_returns_tool_results(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_exa(monkeypatch)
    results = exa_module.search("Acme AI", num_results=2)
    assert len(results) == 2
    assert results[0].url == "https://acme.ai"
    assert results[0].source_kind == "web"
    assert results[0].text == "Acme AI builds tools."
    assert results[0].raw_metadata == {"score": 0.9}


def test_search_fails_open_on_empty_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "ex-test-fake")

    class _EmptyExa:
        def __init__(self, api_key: str) -> None:  # noqa: ARG002
            pass

        def search_and_contents(self, *_a, **_kw):  # noqa: ARG002
            return _FakeExaResponse([])

    import exa_py
    monkeypatch.setattr(exa_py, "Exa", _EmptyExa)
    assert exa_module.search("no such company") == []


def test_missing_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings now validates EXA_API_KEY presence at process init — see
    test_settings_requires_required_keys in test_settings.py for the
    canonical assertion. Here we just confirm read_exa_env's strict=True
    branch still fires when the underlying setting is empty (e.g. empty
    SecretStr after a hot-reload monkeypatch)."""
    from dossier.core.settings import Settings
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.setattr(
        "dossier.core.settings.Settings.model_config",
        {**Settings.model_config, "env_file": None},
    )
    with pytest.raises(Exception, match="exa_api_key|EXA_API_KEY"):
        exa_module.read_exa_env(strict=True)


def test_search_skips_results_with_no_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "ex-test-fake")

    class _PartialExa:
        def __init__(self, api_key: str) -> None:  # noqa: ARG002
            pass

        def search_and_contents(self, *_a, **_kw):  # noqa: ARG002
            return _FakeExaResponse([
                _FakeExaResult(url="", text="no url"),
                _FakeExaResult(url="https://ok.com", text="fine"),
            ])

    import exa_py
    monkeypatch.setattr(exa_py, "Exa", _PartialExa)
    results = exa_module.search("x")
    assert len(results) == 1
    assert results[0].url == "https://ok.com"
