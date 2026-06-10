"""Regression tests for bugs found in the spec-vs-implementation review.

Each test targets a specific defect:
  #1 /builder/batch-assess crashed (stale threshold_rules/raw_value)
  #2 batch_assess wrote a raw dict into node.current, corrupting state
  #3 non-leaf synthesis ignored edge weight (reverted to max under FakeLLM)
  #4 synthesis over-redacted internal causes before the LLM
"""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from foursight.api import build_app
from foursight.assess import assess, redact_cause
from foursight.graph_store import GraphStore
from foursight.models import (
    Node, NodeKind, EdgeType, DataBinding, FieldRule, Severity, Sensitivity,
    Signal, Assessment,
)


# --- Bug #4: cause redaction is clearance-relative, not "anything above public" ---

def test_redact_cause_internal_is_visible():
    sig = Signal(source_node="n", score=50.0, severity=Severity.HIGH,
                 cause="capacity dropped 40%", sensitivity=Sensitivity.INTERNAL)
    assert redact_cause(sig) == "capacity dropped 40%"


def test_redact_cause_confidential_is_hidden():
    sig = Signal(source_node="n", score=50.0, severity=Severity.HIGH,
                 cause="alice salary +6%", sensitivity=Sensitivity.CONFIDENTIAL)
    out = redact_cause(sig)
    assert "REDACTED" in out
    assert "alice" not in out


# --- Bug #3: non-leaf LLM synthesis must weigh edges, even under FakeLLM ---

def _weighted_root_score(weight, llm, vector):
    s = GraphStore()
    s.add_node(Node(id="root", kind=NodeKind.TASK, title="Root"))
    s.add_node(Node(id="leaf", kind=NodeKind.LEAF, title="Leaf",
                    data_binding=DataBinding(adapter_id="leaf", field_rules=[
                        FieldRule(field="effect_score", kind="structured", operator=">=",
                                  expected=50.0, severity_on_breach=Severity.HIGH)],
                        raw_values={"effect_score": 60.0})))
    s.add_edge("root", "leaf", EdgeType.DECOMPOSITION, weight)
    assess(s.get_node("leaf"), s, llm, vector, {})
    a = assess(s.get_node("root"), s, llm, vector, {})
    return a.llm_verdict.final_score


def test_nonleaf_synthesis_is_weight_sensitive(llm, vector):
    critical = _weighted_root_score(Severity.CRITICAL, llm, vector)
    low = _weighted_root_score(Severity.LOW, llm, vector)
    assert critical > low, f"edge weight had no effect: critical={critical} low={low}"


def test_medium_weight_preserves_max_propagation(llm, vector):
    """All-MEDIUM graphs (every fixture) must still behave like max()."""
    medium = _weighted_root_score(Severity.MEDIUM, llm, vector)
    assert medium == 62.0  # HIGH field rule -> 62, passed through unchanged


# --- Bug #1 + #2: /builder/batch-assess runs and writes real Assessments ---

def test_builder_batch_assess_endpoint_runs():
    c = TestClient(build_app())  # default Fab 17 seed: leaves have field_rules
    r = c.post("/builder/batch-assess", json={"mode": "full"})
    assert r.status_code == 200, r.text
    assert "assessments" in r.json()


def test_builder_batch_assess_keeps_graph_readable():
    c = TestClient(build_app())
    c.post("/builder/batch-assess", json={"mode": "full"})
    # If node.current were a raw dict, this would 500 on .llm_verdict access.
    g = c.get("/builder/graph")
    assert g.status_code == 200
    assert any(n["severity"] is not None for n in g.json()["nodes"])


def test_mcp_batch_assess_writes_assessment_objects():
    from foursight.mcp_server import build_mcp
    from foursight.fakes import FakeStore, FakeEngine, fake_get_report, fake_trace
    from foursight.flatten import FlattenEngine
    from foursight.llm import FakeLLM
    import asyncio
    from fastmcp import Client

    store = FakeStore()
    store.add_node(Node(id="t", kind=NodeKind.TASK, title="T", delta_accumulator=60.0))
    mcp = build_mcp(store, FakeEngine(store), fake_get_report, fake_trace,
                    flatten=FlattenEngine(store), llm=FakeLLM())

    async def _go():
        async with Client(mcp) as c:
            await c.call_tool("batch_assess", {"mode": "full"})
    asyncio.run(_go())

    cur = store.get_node("t").current
    assert isinstance(cur, Assessment), f"node.current is {type(cur).__name__}, expected Assessment"
