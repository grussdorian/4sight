"""The demo assessment path must flatten the whole graph and call the LLM
ONCE (batch), not once per node. Tests use a spy LLM that counts calls."""
import json
import re

from fastapi.testclient import TestClient

from foursight.api import build_app
from foursight.seed import load_supply_chain


class SpyLLM:
    """Batch-capable LLM (model != 'fake') that records calls and prompts.
    Per-node methods raise so any per-node assessment is caught."""
    def __init__(self, severity="high"):
        self.model = "spy"
        self.batch_calls = 0
        self.prompts = []
        self.severity = severity

    def batch_assess(self, system, prompt):
        self.batch_calls += 1
        self.prompts.append(prompt)
        # Only the NODE HEADER ids ("... id=X)") are the nodes to score; input
        # references read "id=X, weight=...".
        ids = []
        for m in re.findall(r"id=([A-Za-z0-9_]+)\)", prompt):
            if m not in ids:
                ids.append(m)
        return json.dumps([
            {"node_id": i, "final_score": 80.0, "severity": self.severity,
             "rationale": "spy synthesis"}
            for i in ids
        ])

    def verify_score(self, *a, **k):
        raise AssertionError("per-node verify_score called; expected one batch call")

    def synthesize(self, *a, **k):
        raise AssertionError("per-node synthesize called; expected one batch call")

    def generate_overall(self, *a, **k):
        raise AssertionError("per-node generate_overall called; expected one batch call")

    def summarize(self, node, chunks):
        return "spy context"


def test_assessment_is_a_single_batch_call():
    spy = SpyLLM()
    c = TestClient(build_app(seed_fn=load_supply_chain, llm=spy))
    g = c.post("/assess").json()
    # One call for scoring + one for mitigations = 2
    assert spy.batch_calls in (1, 2), f"expected 1-2 batch calls, got {spy.batch_calls}"
    # Every node got the batch-assigned severity.
    assert all(n["severity"] == "high" for n in g["nodes"])
    # A second assessment with no change does not re-call the LLM.
    c.post("/assess")
    assert spy.batch_calls == 2  # still 2 (1 scoring + 1 mitigation from first run)


def test_fake_llm_still_uses_deterministic_per_node():
    # Hermetic default (FakeLLM) keeps rule-based per-node scoring so the
    # scenario suite stays deterministic.
    c = TestClient(build_app(seed_fn=load_supply_chain))
    root = c.get("/report/fab17_output", params={"role": "reviewer"}).json()
    assert root["severity"] in ("low", "medium")  # healthy baseline


def test_parse_batch_response_robustness():
    from foursight.flatten import FlattenEngine
    from foursight.fakes import FakeStore
    flat = FlattenEngine(FakeStore())
    # clean array
    assert flat.parse_batch_response('[{"node_id":"a","final_score":1}]')[0]["node_id"] == "a"
    # markdown fenced
    fenced = '```json\n[{"node_id":"b","final_score":2}]\n```'
    assert flat.parse_batch_response(fenced)[0]["node_id"] == "b"
    # surrounding prose
    prose = 'Here are the assessments:\n[{"node_id":"c","final_score":3}]\nDone.'
    assert flat.parse_batch_response(prose)[0]["node_id"] == "c"
    # truncated array -> salvage complete objects
    trunc = '[{"node_id":"d","final_score":4},{"node_id":"e","final_score":5},{"node_id":"f"'
    salvaged = flat.parse_batch_response(trunc)
    assert [o["node_id"] for o in salvaged] == ["d", "e"]


def test_inbound_signals_computed_from_influence_after_batch():
    # After a batch assessment (which sets every node's outbound signal), the
    # panel's inbound list must be the node's INPUTS, derived from the influence
    # graph -- not an empty/stale cache and not its consumers.
    spy = SpyLLM()
    c = TestClient(build_app(seed_fn=load_supply_chain, llm=spy))
    c.get("/builder/graph")  # one batch assessment
    log = c.get("/builder/nodes/logistics").json()
    inbound_src = {s["source_node"] for s in log["inbound_signals"]}
    assert inbound_src == {"taipei_freight", "singapore_freight", "bunker_fuel"}, inbound_src
    assert "supply_chain" not in inbound_src and "eng_ops" not in inbound_src


def test_assessment_only_rescore_changed_cone():
    # After the first full assessment, editing one leaf re-scores only that
    # leaf's influence cone; unrelated branches keep their severity.
    spy = SpyLLM(severity="critical")
    c = TestClient(build_app(seed_fn=load_supply_chain, llm=spy))
    g1 = c.post("/assess").json()
    sev1 = {n["id"]: n["severity"] for n in g1["nodes"]}
    assert sev1["logistics"] == "critical" and sev1["fab17_output"] == "critical"

    spy.severity = "low"
    spy.prompts.clear()
    c.post("/node/bob_taylor/readings", json={"readings": {"capacity_pct": 30}})
    g2 = c.post("/assess").json()
    sev2 = {n["id"]: n["severity"] for n in g2["nodes"]}

    assert len(spy.prompts) == 1, "expected exactly one batch call for the cone"
    # bob_taylor's cone (workforce, packaging -> eng_ops -> fab17) was re-scored.
    assert sev2["workforce"] == "low"
    assert sev2["fab17_output"] == "low"
    # The logistics branch was NOT touched and keeps its prior severity.
    assert sev2["logistics"] == "critical"
    assert sev2["sumco_yield"] == "critical"


def test_no_change_means_no_rescore():
    spy = SpyLLM(severity="high")
    c = TestClient(build_app(seed_fn=load_supply_chain, llm=spy))
    c.post("/assess")
    calls = spy.batch_calls
    c.post("/assess")  # nothing changed
    assert spy.batch_calls == calls, "re-assessment with no change must not call the LLM"


def test_history_is_recorded_and_anchors_prompt():
    spy = SpyLLM(severity="critical")
    c = TestClient(build_app(seed_fn=load_supply_chain, llm=spy))
    c.post("/assess")            # records each node's judgment
    spy.prompts.clear()
    c.post("/node/sumco_yield/readings", json={"readings": {"yield_pct": 40}})
    c.post("/assess")            # re-score cone; prompt should carry past judgments
    assert any("Past judgments" in p for p in spy.prompts), "history not fed back into the prompt"
