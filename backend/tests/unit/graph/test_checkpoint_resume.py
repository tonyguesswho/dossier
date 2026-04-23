"""Checkpoint-resume test for LangGraph (ROADMAP SC#3 / BLOCKER-1).

Verifies that when a graph run is interrupted mid-execution and re-invoked
with the same thread_id, completed nodes are NOT re-executed.

Uses MemorySaver (in-process) — the checkpoint-resume semantics are identical
to AsyncPostgresSaver; only the storage backend differs. This test exercises
the LangGraph checkpoint protocol, not the DB adapter.

Test strategy:
    1. Build a minimal StateGraph with two nodes: counter_node + crash_node.
    2. counter_node increments a counter in state on each call.
    3. crash_node raises RuntimeError on first invocation, passes on second.
    4. First graph.ainvoke(initial_state, ...): counter_node runs (count=1),
       crash_node raises. Checkpoint persists state after counter_node.
    5. Second graph.ainvoke(None, config=config_with_same_thread_id):
       passing None as input tells LangGraph to CONTINUE from the last
       checkpoint rather than start a fresh run. counter_node is NOT
       re-executed; crash_node re-runs (its earlier execution failed
       before writing its own checkpoint) and this time passes.

Why MemorySaver is sufficient here (not AsyncPostgresSaver):
    LangGraph's checkpoint protocol is backend-agnostic. MemorySaver,
    AsyncPostgresSaver, and SqliteSaver all implement BaseCheckpointSaver
    and use the same get_tuple / put / put_writes protocol. If resume works
    against MemorySaver, it works against AsyncPostgresSaver. Spinning up
    real Postgres for this test would (a) couple the unit test to docker,
    and (b) re-verify library behavior rather than our integration.

Why `ainvoke(None, config)` is the correct resume call:
    LangGraph's ainvoke contract: a dict input re-runs from START with that
    input (useful for a fresh investigation); None input continues from the
    last checkpoint on the given thread_id (useful for Lambda re-drive).
    runner.py detects the resume case via aget_state(config).next being
    non-empty (the node that was about to run when the crash happened).
"""
from __future__ import annotations

import asyncio
from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph


class _TestState(TypedDict):
    counter: int
    crash_count: int  # how many times crash_node has been called


# Module-level counter for crash_node — tracks real function calls across
# both invocations. A node that was skipped on resume will NOT bump this.
_call_tracker: dict[str, int] = {"crash_node_calls": 0, "counter_node_calls": 0}


async def _counter_node(state: _TestState) -> dict:
    """Increments counter. Must run exactly once even with resume."""
    _call_tracker["counter_node_calls"] += 1
    return {"counter": state.get("counter", 0) + 1}


async def _crash_node(state: _TestState) -> dict:
    """Raises on first call; passes on second (simulates Lambda kill + resume)."""
    count = _call_tracker["crash_node_calls"]
    _call_tracker["crash_node_calls"] = count + 1

    if count == 0:
        raise RuntimeError("simulated Lambda kill mid-graph")

    return {"crash_count": count + 1}


def _build_test_graph():
    builder = StateGraph(_TestState)
    builder.add_node("counter_node", _counter_node)
    builder.add_node("crash_node", _crash_node)
    builder.add_edge(START, "counter_node")
    builder.add_edge("counter_node", "crash_node")
    builder.add_edge("crash_node", END)
    return builder.compile(checkpointer=MemorySaver())


def test_resume_skips_completed_nodes():
    """After a mid-graph crash, resuming with same thread_id must not re-run completed nodes.

    Asserts:
      - counter_node's real function is called exactly once across two invocations.
      - state.counter is 1 (counter_node's checkpointed output, not a re-run doubling).
      - crash_node was called twice: once to fail, once to succeed.
      - After the first crash, graph.aget_state(config).next is non-empty — the
        resume detection that runner.py uses to decide between fresh-invoke
        (dict input) and resume-invoke (None input).

    This is the Phase 3 equivalent of ROADMAP SC#3: LangGraph's checkpointing
    means a Lambda kill mid-investigation resumes from the last durable
    checkpoint, not from the top of the graph.
    """
    _call_tracker["crash_node_calls"] = 0
    _call_tracker["counter_node_calls"] = 0

    graph = _build_test_graph()
    thread_id = "test-investigation-resume"
    config = {"configurable": {"thread_id": thread_id}}

    initial_state: _TestState = {"counter": 0, "crash_count": 0}

    # First invocation: counter_node runs (counter → 1), crash_node raises.
    with pytest.raises(RuntimeError, match="simulated Lambda kill"):
        asyncio.run(graph.ainvoke(initial_state, config=config))

    # Counter node ran exactly once in the first invocation.
    assert _call_tracker["counter_node_calls"] == 1

    # Resume-detection sanity check — mirrors runner.py's is_resume logic.
    # An unseen thread has next=() and created_at=None; an interrupted thread
    # has next=('<node_name>',) pointing at the node that would have run next.
    snapshot = graph.get_state(config)
    assert snapshot.next, (
        f"Expected snapshot.next to be non-empty after mid-graph crash; got {snapshot.next}. "
        "runner.py's is_resume detection depends on this signal."
    )
    assert snapshot.created_at is not None, (
        "Expected snapshot.created_at to be set after first invocation persisted a checkpoint."
    )

    # Second invocation: pass None as input to RESUME from the last checkpoint.
    # This mirrors runner.py's graph_input = None branch when is_resume is True.
    # Passing initial_state again would re-run counter_node from START; None
    # tells LangGraph "continue from where you left off on this thread_id".
    final_state = asyncio.run(graph.ainvoke(None, config=config))

    assert _call_tracker["counter_node_calls"] == 1, (
        f"counter_node was re-executed on resume "
        f"(called {_call_tracker['counter_node_calls']} times). "
        "LangGraph checkpoint-resume must skip already-completed nodes (ROADMAP SC#3)."
    )
    assert final_state["counter"] == 1, (
        f"counter_node's checkpointed output was lost (counter={final_state['counter']}). "
        "Expected 1 — checkpoint should preserve completed nodes' state."
    )
    assert _call_tracker["crash_node_calls"] == 2, (
        f"crash_node should have been called twice (once failing, once passing); "
        f"actual call count: {_call_tracker['crash_node_calls']}"
    )
    assert final_state["crash_count"] >= 1, (
        "crash_node should have completed on second invocation"
    )
