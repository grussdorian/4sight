"""The demo assessment path must flatten the whole graph and call the LLM
ONCE (batch), not once per node. Tests use a spy LLM that counts calls."""
import json
import re

from fastapi.testclient import TestClient

from foursight.api import build_app
from foursight.seed import load_supply_chain


class SpyLLM:
    """Batch-capable LLM (model != 'fake') that records call counts. Per-node
    methods raise so any per-node assessment is caught."""
    def __init__(self):
        self.model = "spy"
        self.batch_calls = 0

    def batch_assess(self, system, prompt):
        self.batch_calls += 1
        ids = []
        for m in re.findall(r"id=([A-Za-z0-9_]+)", prompt):
            if m not in ids:
                ids.append(m)
        return json.dumps([
            {"node_id": i, "final_score": 80.0, "severity": "high",
             "rationale": "spy synthesis", "summary": "spy summary"}
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
    # First read triggers the (lazy) assessment.
    g = c.get("/builder/graph").json()
    assert spy.batch_calls == 1, f"expected exactly one batch call, got {spy.batch_calls}"
    # Every node got the batch-assigned severity.
    assert all(n["severity"] == "high" for n in g["nodes"])
    # A second read does not re-assess.
    c.get("/builder/graph")
    assert spy.batch_calls == 1


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
