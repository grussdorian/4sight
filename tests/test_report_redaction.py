"""Confidential leaf nodes (Alice Chen, Bob Taylor -- real people) must not leak
their underlying data to a reviewer. The privilege model is: reviewers see
severity only; privileged sees the full data (readings, field rules, query).
The report panel reads /builder/nodes/{id} and the canvas reads /builder/graph,
so BOTH must redact leaf data by sensitivity."""
from fastapi.testclient import TestClient

from foursight.api import build_app
from foursight.seed import load_supply_chain


QUAL_RULE = {"field": "takes_leave", "kind": "unstructured",
             "severity_on_breach": "critical"}


def test_confidential_leaf_data_redacted_for_reviewer():
    c = TestClient(build_app(seed_fn=load_supply_chain))
    # Assert a qualitative rule on Alice (confidential in the fixture).
    c.post("/builder/nodes", json={
        "id": "alice_chen", "kind": "leaf", "title": "Alice Chen, Process Lead",
        "adapter_id": "alice_chen", "query": "", "field_rules": [QUAL_RULE],
    })

    priv = c.get("/builder/nodes/alice_chen?role=privileged").json()
    rev = c.get("/builder/nodes/alice_chen?role=reviewer").json()

    # Editing must NOT downgrade her confidential sensitivity to internal.
    assert priv["data_restricted"] is False
    assert any(fr["field"] == "takes_leave" for fr in priv["field_rules"]), \
        "privileged must see the data"

    # Reviewer sees severity only -- no field rules, no readings, no query.
    assert rev["data_restricted"] is True
    assert rev["field_rules"] == []
    assert rev["raw_values"] == {}
    assert rev["query"] == ""
    assert rev["severity"] == priv["severity"]  # severity is visible to both


def test_builder_graph_redacts_confidential_for_reviewer():
    c = TestClient(build_app(seed_fn=load_supply_chain))
    c.post("/builder/nodes", json={
        "id": "alice_chen", "kind": "leaf", "title": "Alice Chen, Process Lead",
        "adapter_id": "alice_chen", "query": "", "field_rules": [QUAL_RULE],
    })

    g_rev = c.get("/builder/graph?role=reviewer").json()
    g_priv = c.get("/builder/graph?role=privileged").json()
    alice_rev = next(n for n in g_rev["nodes"] if n["id"] == "alice_chen")
    alice_priv = next(n for n in g_priv["nodes"] if n["id"] == "alice_chen")

    assert alice_rev["field_rules"] == []
    assert alice_rev["raw_values"] == {}
    assert any(fr["field"] == "takes_leave" for fr in alice_priv["field_rules"])


def test_non_confidential_leaf_visible_to_reviewer():
    # An internal leaf (tsmc_wafer) is NOT restricted -- redaction is by
    # sensitivity, not a blanket block on all leaves.
    c = TestClient(build_app(seed_fn=load_supply_chain))
    rev = c.get("/builder/nodes/tsmc_wafer?role=reviewer").json()
    assert rev["data_restricted"] is False
