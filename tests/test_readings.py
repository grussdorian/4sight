from fastapi.testclient import TestClient

from foursight.api import build_app
from foursight.seed import load_supply_chain


def _client():
    return TestClient(build_app(seed_fn=load_supply_chain))


def test_set_readings_persists_and_changes_severity():
    c = _client()
    c.get("/builder/graph")  # baseline assessment (healthy)
    assert c.get("/report/sumco_yield", params={"role": "privileged"}).json()["severity"] == "low"

    r = c.post("/node/sumco_yield/readings", json={"readings": {"yield_pct": 55}})
    assert r.status_code == 200
    assert r.json()["raw_values"]["yield_pct"] == 55.0

    c.post("/assess")  # re-read + re-assess
    sev = c.get("/report/sumco_yield", params={"role": "privileged"}).json()["severity"]
    assert sev in ("high", "critical"), sev


def test_reading_override_survives_reassessment():
    # Editing a reading writes to leaf_metrics as an override that wins over the
    # node's own query result, so it persists through a re-read.
    c = _client()
    c.post("/node/sumco_yield/readings", json={"readings": {"yield_pct": 45}})
    c.post("/assess")
    # sumco_yield is confidential; read its data as privileged.
    d = c.get("/builder/nodes/sumco_yield?role=privileged").json()
    assert d["raw_values"]["yield_pct"] == 45.0


def test_test_query_runs_select():
    c = _client()
    q = "SELECT 'ferry_price' AS field, price AS value FROM ferry_prices WHERE route = 'taipei'"
    r = c.post("/test-query", json={"query": q})
    assert r.status_code == 200
    assert r.json()["readings"]["ferry_price"] == 75.0


def test_test_query_rejects_non_select():
    c = _client()
    r = c.post("/test-query", json={"query": "DROP TABLE leaf_metrics"})
    assert r.status_code == 400


def test_assess_endpoint_returns_graph():
    c = _client()
    r = c.post("/assess")
    assert r.status_code == 200
    assert len(r.json()["nodes"]) == 20
