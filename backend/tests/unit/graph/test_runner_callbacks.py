"""Tests for Langfuse CallbackHandler plumbing in runner.run_graph.

Verifies trace_id flow from the investigations row into the handler factory,
that config['callbacks'] is [] (not missing) on Langfuse outage, and that
session metadata is set so per-investigation spans group in the Langfuse UI.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest

from dossier.investigate.graph import runner as runner_mod


class _FakeResult:
    def __init__(self, row: tuple | None) -> None:
        self._row = row

    def fetchone(self) -> tuple | None:
        return self._row


class _FakeSession:
    """Returns one row per invocation; drops awaits cleanly."""

    def __init__(self, row: tuple | None) -> None:
        self._row = row

    async def execute(self, *_args: Any, **_kwargs: Any) -> _FakeResult:
        return _FakeResult(self._row)

    # The finalize-path UPDATE uses session.begin() inside a context manager;
    # the happy path in this test never reaches that code because we
    # short-circuit graph.ainvoke with a successful AsyncMock.
    def begin(self) -> "_FakeSessionBeginCtx":
        return _FakeSessionBeginCtx()


class _FakeSessionBeginCtx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_args: Any) -> None:
        return None


def _make_session_cm(row: tuple | None):
    """Build an async context manager that yields a _FakeSession with `row`."""

    @asynccontextmanager
    async def _cm():
        yield _FakeSession(row)

    return _cm


class _SpyGraph:
    """Stand-in for a compiled StateGraph that records ainvoke() call args."""

    def __init__(self) -> None:
        self.ainvoke_calls: list[tuple[Any, dict[str, Any]]] = []
        self.aget_state_calls: list[dict[str, Any]] = []

    async def aget_state(self, config: dict[str, Any]) -> MagicMock:
        self.aget_state_calls.append(config)
        snap = MagicMock()
        snap.next = ()
        snap.created_at = None
        return snap

    async def ainvoke(self, graph_input: Any, config: dict[str, Any]) -> dict:
        self.ainvoke_calls.append((graph_input, config))
        return {"ok": True}


@pytest.fixture
def patched_runner(monkeypatch):
    """Monkeypatches heavy deps on runner_mod's import paths + yields a handle.

    Callers configure:
      - handle["row"]: the investigations-row tuple _FakeSession returns
      - handle["handler"]: value get_langchain_callback_handler returns
    Then call handle["run"]() to invoke run_graph under test.
    """
    spy_graph = _SpyGraph()
    handle: dict[str, Any] = {
        "row": None,
        "handler": None,
        "spy_graph": spy_graph,
        "handler_calls": [],
    }

    def _fake_factory(*args, **kwargs):
        handle["handler_calls"].append(kwargs)
        return handle["handler"]

    async def _fake_checkpointer() -> object:
        return object()

    def _fake_build_graph(*_a: Any, **_kw: Any) -> _SpyGraph:
        return spy_graph

    # runner.py imports these deferred inside run_graph() so we patch at
    # the module that run_graph imports from.
    from dossier.core import db as db_mod
    from dossier.investigate import graph as graph_pkg
    from dossier import observability as obs_mod

    def _install():
        monkeypatch.setattr(obs_mod, "get_langfuse_client",
                            lambda strict=False: None)
        monkeypatch.setattr(obs_mod, "get_langchain_callback_handler",
                            _fake_factory)
        monkeypatch.setattr(
            db_mod, "get_async_session", _make_session_cm(handle["row"])
        )
        monkeypatch.setattr(runner_mod, "_get_checkpointer", _fake_checkpointer)
        monkeypatch.setattr(graph_pkg, "build_graph", _fake_build_graph)

    handle["install"] = _install
    handle["run"] = lambda inv_id="inv-1": asyncio.run(runner_mod.run_graph(inv_id))
    return handle


def test_callbacks_populated_when_handler_returned(patched_runner):
    """Happy path: handler returned → config['callbacks'] == [handler]."""
    fake_handler = object()
    patched_runner["row"] = (
        "name",                           # input_type
        "Stripe",                         # input_ref (real rows may carry an InvestigationInput-encoded hint tail)
        "lf-trace-abc-123",               # langfuse_trace_id
    )
    patched_runner["handler"] = fake_handler
    patched_runner["install"]()

    patched_runner["run"]("inv-42")

    spy = patched_runner["spy_graph"]
    assert len(spy.ainvoke_calls) == 1
    _input, config = spy.ainvoke_calls[0]
    assert config["callbacks"] == [fake_handler]


def test_callbacks_empty_list_when_handler_none(patched_runner):
    """Langfuse outage: factory → None → callbacks is [], not [None], not missing."""
    patched_runner["row"] = ("name", "Linear", "lf-trace-no-handler")
    patched_runner["handler"] = None  # simulate creds absent / outage
    patched_runner["install"]()

    patched_runner["run"]("inv-7")

    spy = patched_runner["spy_graph"]
    _input, config = spy.ainvoke_calls[0]
    assert "callbacks" in config
    assert config["callbacks"] == []
    assert None not in config["callbacks"]


def test_handler_factory_called_with_row_trace_id(patched_runner):
    """Factory receives trace_id from the investigations row (D-03)."""
    patched_runner["row"] = ("name", "Anthropic", "lf-trace-xyz-777")
    patched_runner["handler"] = object()
    patched_runner["install"]()

    patched_runner["run"]("inv-99")

    factory_kwargs = patched_runner["handler_calls"][0]
    assert factory_kwargs["trace_id"] == "lf-trace-xyz-777"
    assert factory_kwargs["session_id"] == "inv-99"
    assert factory_kwargs["strict"] is False


def test_config_carries_session_id_metadata(patched_runner):
    """4.x session plumbing: config['metadata']['langfuse_session_id'] is set."""
    patched_runner["row"] = ("name", "OpenAI", "lf-trace-meta-001")
    patched_runner["handler"] = object()
    patched_runner["install"]()

    patched_runner["run"]("inv-meta-1")

    _input, config = patched_runner["spy_graph"].ainvoke_calls[0]
    assert config.get("metadata", {}).get("langfuse_session_id") == "inv-meta-1"


def test_thread_id_preserved_in_configurable(patched_runner):
    """Adding callbacks/metadata must not disturb configurable.thread_id (resume)."""
    patched_runner["row"] = ("name", "Mistral", "lf-trace-thread")
    patched_runner["handler"] = None
    patched_runner["install"]()

    patched_runner["run"]("inv-thread-1")

    _input, config = patched_runner["spy_graph"].ainvoke_calls[0]
    assert config["configurable"]["thread_id"] == "inv-thread-1"


def test_missing_investigation_row_short_circuits_without_ainvoke(patched_runner):
    """If the row lookup returns None, run_graph returns early without invoking the graph."""
    patched_runner["row"] = None
    patched_runner["handler"] = None
    patched_runner["install"]()

    # Should not raise — matches runner.py's existing "not found" branch.
    patched_runner["run"]("inv-missing")

    assert patched_runner["spy_graph"].ainvoke_calls == []
    assert patched_runner["handler_calls"] == []
