from fastapi.testclient import TestClient

from foursight.api import build_app
from foursight.seed import load_supply_chain


def _client():
    # db_path=None -> in-memory conn, FakeLLM/FakeVector via seed_fn (hermetic)
    return TestClient(build_app(seed_fn=load_supply_chain))


def test_inject_high_drives_node_and_cascades():
    c = _client()
    r = c.post("/inject/sumco_yield", json={"severity": "high"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["field"] == "yield_pct"
    assert body["value"] < 60  # HIGH band (< 60, >= 50)
    assert "sumco_yield" in body["changed"]
    sev = c.get("/report/sumco_yield", params={"role": "privileged"}).json()["severity"]
    assert sev in ("high", "critical")
    root = c.get("/report/fab17_output", params={"role": "reviewer"}).json()
    assert root["severity"] in ("medium", "high", "critical")


def test_inject_critical_then_low_resets():
    c = _client()
    c.post("/inject/sumco_yield", json={"severity": "critical"})
    hi = c.get("/report/sumco_yield", params={"role": "privileged"}).json()["severity"]
    assert hi == "critical"
    c.post("/inject/sumco_yield", json={"severity": "low"})
    lo = c.get("/report/sumco_yield", params={"role": "privileged"}).json()["severity"]
    assert lo == "low"


def test_inject_high_direction_field():
    c = _client()
    r = c.post("/inject/bunker_fuel", json={"severity": "high"})
    body = r.json()
    assert body["field"] == "fuel_price"
    assert body["value"] > 65  # higher-is-worse HIGH band


def test_poll_endpoint_returns_changed_list():
    c = _client()
    r = c.post("/poll", json={})
    assert r.status_code == 200
    assert isinstance(r.json()["changed"], list)


def test_node_context_returns_summary():
    c = _client()
    r = c.get("/node/lithography/context")
    assert r.status_code == 200
    assert r.json()["summary"]  # FakeLLM template, non-empty


def test_builder_node_inputs_consumers_direction():
    c = _client()
    fab = c.get("/builder/nodes/fab17_output").json()
    # Fab 17 is the root: it depends on (inputs) eng_ops/supply_chain/workforce,
    # and nothing consumes it.
    assert set(fab["inputs"]) >= {"eng_ops", "supply_chain", "workforce"}
    assert fab["consumers"] == []
    sumco = c.get("/builder/nodes/sumco_yield").json()
    assert "sumco_yield" not in sumco["inputs"]
    assert sumco["consumers"]  # leaf feeds upstream consumers


def test_persistence_survives_restart(tmp_path):
    db = str(tmp_path / "fab.db")
    c1 = TestClient(build_app(seed_fn=load_supply_chain, db_path=db))
    c1.post("/inject/sumco_yield", json={"severity": "critical"})
    sev1 = c1.get("/report/sumco_yield", params={"role": "privileged"}).json()["severity"]
    assert sev1 == "critical"
    # Fresh app instance on the same DB file: structure + injected metric restored.
    c2 = TestClient(build_app(seed_fn=load_supply_chain, db_path=db))
    sev2 = c2.get("/report/sumco_yield", params={"role": "privileged"}).json()["severity"]
    assert sev2 == "critical", "injected problem did not persist across restart"
    # structure restored too
    fab = c2.get("/builder/nodes/fab17_output").json()
    assert set(fab["inputs"]) >= {"eng_ops", "supply_chain", "workforce"}


def test_root_endpoint_returns_true_root():
    c = _client()
    assert c.get("/root").json()["node_id"] == "fab17_output"


def test_inbound_signals_are_inputs_not_consumers():
    c = _client()
    c.get("/builder/graph")  # trigger assessment
    wf = c.get("/builder/nodes/workforce").json()
    inbound_src = {s["source_node"] for s in wf["inbound_signals"]}
    # workforce is fed by bob_taylor + maint_crew; fab17_output CONSUMES it.
    assert inbound_src == {"bob_taylor", "maint_crew"}, inbound_src
    assert "fab17_output" not in inbound_src
    assert "fab17_output" in wf["consumers"]


def test_logistics_inbound_excludes_consumers():
    c = _client()
    c.get("/builder/graph")
    log = c.get("/builder/nodes/logistics").json()
    inbound_src = {s["source_node"] for s in log["inbound_signals"]}
    assert inbound_src <= {"taipei_freight", "singapore_freight", "bunker_fuel"}, inbound_src
    assert "supply_chain" not in inbound_src and "eng_ops" not in inbound_src
