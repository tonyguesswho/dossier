"""Unit tests for the founder_extraction graph node (Plan 03-05 / D-05).

Contract under test:
  - Happy path: returns up to 5 founder names extracted by the LLM.
  - Cap at 5: even if the LLM returns 10, only 5 are propagated (D-05 +
    T-03-05-03 — bounds the Stage-2 GitHub fan-out).
  - Fail-open: any SDK exception → empty founder_candidates list (T-03-05-02:
    do not invent founder names; stage2_router sends only Crunchbase).
  - Refusal handling: model refusal → empty list (same rationale).
  - parsed=None → empty list (malformed LLM output must not crash the graph).
  - Empty founders array → empty list (LLM's correct answer when uncertain).
  - Client injection: the `client=` kwarg bypasses strong_model() so tests
    never touch OpenRouter.

No real OpenAI calls — fake client with scripted responses.
Target runtime: <100ms.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from dossier.investigate.graph.nodes import founder_extraction
from dossier.investigate.graph.state import FounderCandidate, FounderCandidates


def _state(**overrides: Any) -> dict:
    """Build a minimal DossierState-shaped dict."""
    base = {
        "investigation_id": "test-inv",
        "company": "Acme Co",
        "context_hint": None,
        "input_url": None,
        "reflection_count": 0,
        "should_regather": False,
        "targeted_sections": [],
        "founder_candidates": [],
        "retrieved_chunks": [],
        "draft_claims": [],
        "grounded_claims": [],
    }
    base.update(overrides)
    return base


class _FakeClient:
    """Fake OpenAI client exposing `.beta.chat.completions.parse(...)` only.

    Scripted `parsed` value is returned on every call; `refusal` attribute
    is settable to simulate content-policy refusal.
    """

    def __init__(
        self,
        parsed: FounderCandidates | None = None,
        refusal: str | None = None,
        raise_exc: BaseException | None = None,
    ) -> None:
        self._parsed = parsed
        self._refusal = refusal
        self._raise_exc = raise_exc
        self.captured_model: str | None = None
        self.captured_messages: list[dict] | None = None
        self.captured_response_format: Any = None

    @property
    def beta(self):  # noqa: D401
        return SimpleNamespace(chat=SimpleNamespace(completions=self))

    def parse(self, *, model, messages, response_format, **_kwargs):
        if self._raise_exc is not None:
            raise self._raise_exc
        self.captured_model = model
        self.captured_messages = messages
        self.captured_response_format = response_format
        message = SimpleNamespace(parsed=self._parsed, refusal=self._refusal)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
def test_happy_path_returns_extracted_names() -> None:
    client = _FakeClient(
        parsed=FounderCandidates(
            founders=[
                FounderCandidate(name="Ada Lovelace", confidence="high"),
                FounderCandidate(name="Grace Hopper", confidence="medium"),
            ]
        )
    )
    result = asyncio.run(founder_extraction.run(_state(), client=client))
    assert result == {"founder_candidates": ["Ada Lovelace", "Grace Hopper"]}


def test_uses_cheap_model_id() -> None:
    """D-05: founder extraction MUST route to Haiku 4.5, never Sonnet."""
    from dossier.core.llm import CHEAP_MODEL_ID

    client = _FakeClient(parsed=FounderCandidates(founders=[]))
    asyncio.run(founder_extraction.run(_state(), client=client))
    assert client.captured_model == CHEAP_MODEL_ID


def test_uses_founder_candidates_response_format() -> None:
    """Structured output must be the FounderCandidates Pydantic model."""
    client = _FakeClient(parsed=FounderCandidates(founders=[]))
    asyncio.run(founder_extraction.run(_state(), client=client))
    assert client.captured_response_format is FounderCandidates


# ---------------------------------------------------------------------------
# D-05 cap + T-03-05-03: at most 5 names regardless of LLM output
# ---------------------------------------------------------------------------
def test_caps_founders_at_five() -> None:
    client = _FakeClient(
        parsed=FounderCandidates(
            founders=[
                FounderCandidate(name=f"Person {i}", confidence="low")
                for i in range(10)
            ]
        )
    )
    result = asyncio.run(founder_extraction.run(_state(), client=client))
    assert result["founder_candidates"] == [f"Person {i}" for i in range(5)]


def test_strips_and_filters_blank_names() -> None:
    """Whitespace-only names must not leak into Stage-2 GitHub queries."""
    client = _FakeClient(
        parsed=FounderCandidates(
            founders=[
                FounderCandidate(name="  Ada Lovelace  ", confidence="high"),
                FounderCandidate(name="   ", confidence="low"),
                FounderCandidate(name="Grace Hopper", confidence="medium"),
            ]
        )
    )
    result = asyncio.run(founder_extraction.run(_state(), client=client))
    assert result == {"founder_candidates": ["Ada Lovelace", "Grace Hopper"]}


# ---------------------------------------------------------------------------
# Fail-open paths (T-03-05-02): never fabricate, always return [] on failure
# ---------------------------------------------------------------------------
def test_exception_returns_empty_list() -> None:
    client = _FakeClient(raise_exc=RuntimeError("openrouter 503"))
    result = asyncio.run(founder_extraction.run(_state(), client=client))
    assert result == {"founder_candidates": []}


def test_refusal_returns_empty_list() -> None:
    client = _FakeClient(parsed=None, refusal="I cannot comply with this request.")
    result = asyncio.run(founder_extraction.run(_state(), client=client))
    assert result == {"founder_candidates": []}


def test_parsed_none_returns_empty_list() -> None:
    client = _FakeClient(parsed=None, refusal=None)
    result = asyncio.run(founder_extraction.run(_state(), client=client))
    assert result == {"founder_candidates": []}


def test_empty_founders_array_returns_empty_list() -> None:
    """LLM says 'I'm uncertain' (empty array) → [] is the correct pass-through."""
    client = _FakeClient(parsed=FounderCandidates(founders=[]))
    result = asyncio.run(founder_extraction.run(_state(), client=client))
    assert result == {"founder_candidates": []}


# ---------------------------------------------------------------------------
# Prompt assembly — URLs only, no raw text (ARCHITECTURE.md §9)
# ---------------------------------------------------------------------------
def test_prompt_includes_company_and_urls() -> None:
    from dossier.investigate.graph.state import RetrievedChunkRef

    client = _FakeClient(parsed=FounderCandidates(founders=[]))
    chunks = [
        RetrievedChunkRef(
            chunk_id="",
            source_id="",
            url="https://acme.ai/about",
            source_kind="web",
            char_start=0,
            char_end=100,
            section_hint="general",
        ),
        RetrievedChunkRef(
            chunk_id="",
            source_id="",
            url="https://news.example.com/acme",
            source_kind="news",
            char_start=0,
            char_end=100,
            section_hint="general",
        ),
    ]
    state = _state(company="Acme Co", retrieved_chunks=chunks)
    asyncio.run(founder_extraction.run(state, client=client))

    user_msg = next(m for m in client.captured_messages if m["role"] == "user")
    assert "Acme Co" in user_msg["content"]
    assert "https://acme.ai/about" in user_msg["content"]
    assert "https://news.example.com/acme" in user_msg["content"]


def test_prompt_handles_no_urls_gracefully() -> None:
    """Empty retrieved_chunks → prompt still well-formed."""
    client = _FakeClient(parsed=FounderCandidates(founders=[]))
    asyncio.run(founder_extraction.run(_state(), client=client))
    user_msg = next(m for m in client.captured_messages if m["role"] == "user")
    assert "no URLs gathered yet" in user_msg["content"]


def test_context_hint_included_when_present() -> None:
    client = _FakeClient(parsed=FounderCandidates(founders=[]))
    state = _state(context_hint="Seed-stage fintech pitch")
    asyncio.run(founder_extraction.run(state, client=client))
    user_msg = next(m for m in client.captured_messages if m["role"] == "user")
    assert "Seed-stage fintech pitch" in user_msg["content"]


# ---------------------------------------------------------------------------
# Async contract
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_run_is_a_coroutine() -> None:
    """run() must be awaitable — graph invokes it as an async node."""
    client = _FakeClient(parsed=FounderCandidates(founders=[]))
    coro = founder_extraction.run(_state(), client=client)
    assert asyncio.iscoroutine(coro)
    result = await coro
    assert result == {"founder_candidates": []}
