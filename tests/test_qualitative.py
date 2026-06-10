"""Qualitative (human) leaf nodes: Alice Chen and Bob Taylor are people, not SQL
data sources. An admin asserts a qualitative risk factor on them by adding an
unstructured field rule (e.g. "takes_leave -> critical"). That assertion must:
  1. persist (editing an existing query-less leaf must not be deduped as a
     duplicate of itself), and
  2. score the leaf and propagate up the graph on the next assessment.
"""
import json
import re

from fastapi.testclient import TestClient

from foursight.api import build_app
from foursight.seed import load_supply_chain
from foursight.models import Node, NodeKind, DataBinding, FieldRule, Severity
from foursight.rules import score_leaf


QUAL_RULE = {"field": "takes_leave", "kind": "unstructured",
             "severity_on_breach": "critical"}


def test_score_leaf_honors_qualitative_rule():
    """An active unstructured rule contributes its severity (its presence asserts
    the condition); it is no longer silently skipped."""
    binding = DataBinding(adapter_id="alice_chen", query="",
                          field_rules=[FieldRule(field="takes_leave",
                                                 kind="unstructured",
                                                 severity_on_breach=Severity.CRITICAL)])
    alice = Node(id="alice_chen", kind=NodeKind.LEAF, title="Alice", data_binding=binding)
    r = score_leaf(alice)
    assert r.score == 88.0  # critical band
    breached_fields = {b["field"] for b in r.inputs.get("breached", [])}
    assert "takes_leave" in breached_fields


def test_qualitative_rule_persists_on_edit():
    """Adding a rule to an existing query-less leaf must not self-dedup; the rule
    is stored and read back."""
    c = TestClient(build_app(seed_fn=load_supply_chain))
    # Read back as privileged: Alice is confidential, so a reviewer's view of her
    # field rules is redacted (covered in test_report_redaction).
    before = c.get("/builder/nodes/alice_chen?role=privileged").json()
    assert before["field_rules"] == []  # qualitative leaf starts empty

    resp = c.post("/builder/nodes", json={
        "id": "alice_chen", "kind": "leaf", "title": before["title"],
        "adapter_id": before.get("adapter_id", "alice_chen"), "query": "",
        "field_rules": [QUAL_RULE],
    }).json()
    assert resp.get("deduped") is not True, "edit was wrongly deduped against itself"

    after = c.get("/builder/nodes/alice_chen?role=privileged").json()
    fields = {fr["field"] for fr in after["field_rules"]}
    assert "takes_leave" in fields, "qualitative rule did not persist"


def test_qualitative_rule_propagates_after_assess():
    """After asserting 'Alice takes leave -> critical' and running an assessment,
    Alice scores critical and her consumers are elevated above LOW."""
    c = TestClient(build_app(seed_fn=load_supply_chain))
    c.post("/builder/nodes", json={
        "id": "alice_chen", "kind": "leaf", "title": "Alice Chen, Process Lead",
        "adapter_id": "alice_chen", "query": "", "field_rules": [QUAL_RULE],
    })
    g = c.post("/assess").json()
    sev = {n["id"]: n["severity"] for n in g["nodes"]}
    assert sev["alice_chen"] == "critical", sev["alice_chen"]
    # lithography and packaging consume Alice; at least one must be elevated.
    assert sev["packaging"] != "low" or sev["lithography"] != "low", sev


class _SpyLowLLM:
    """Batch LLM that scores EVERY node low. Used to prove the qualitative leaf
    is pinned to its rule severity deterministically, independent of the LLM."""
    model = "spy"

    def batch_assess(self, system, prompt):
        ids = []
        for m in re.findall(r"id=([A-Za-z0-9_]+)\)", prompt):
            if m not in ids:
                ids.append(m)
        return json.dumps([{"node_id": i, "final_score": 5.0, "severity": "low",
                            "rationale": "spy"} for i in ids])

    def summarize(self, node, chunks):
        return "spy"

    def synthesize(self, *a, **k):
        raise AssertionError("per-node synthesize called; expected one batch call")

    def verify_score(self, *a, **k):
        raise AssertionError("per-node verify_score called")


def test_qualitative_leaf_pinned_in_batch_path():
    """In the DeepSeek (batch) path, an active qualitative rule pins the leaf to
    its rule severity even when the LLM scores it low."""
    c = TestClient(build_app(seed_fn=load_supply_chain, llm=_SpyLowLLM()))
    c.post("/builder/nodes", json={
        "id": "alice_chen", "kind": "leaf", "title": "Alice Chen, Process Lead",
        "adapter_id": "alice_chen", "query": "", "field_rules": [QUAL_RULE],
    })
    g = c.post("/assess").json()
    sev = {n["id"]: n["severity"] for n in g["nodes"]}
    assert sev["alice_chen"] == "critical", sev["alice_chen"]
