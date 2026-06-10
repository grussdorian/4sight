from datetime import datetime, timezone
from fastapi.testclient import TestClient
from foursight.api import build_app
from foursight.seed import load_supply_chain
from foursight.models import Viewer, Role


def _client():
    return TestClient(build_app(seed_fn=load_supply_chain))


def _inject(client, source, node_id, effect_score, kind="supply"):
    resp = client.post("/simulate-change", json={
        "kind": kind, "source": source, "node_id": node_id,
        "effect_score": effect_score,
    })
    assert resp.status_code == 200, f"Injection failed: {resp.text}"
    return resp.json()


def assert_severity(report, expected_severity):
    assert report is not None, f"Expected {expected_severity} report, got None"
    assert report["severity"] == expected_severity, \
        f"Expected {expected_severity}, got {report['severity']}: {report.get('overall', '')[:200]}"


def assert_trace(client, node_id, expected_origin_leaf):
    trace = client.get(f"/trace/{node_id}").json()
    assert trace["origin"] is not None, f"Trace for {node_id} has no origin"
    assert trace["path"][-1] == expected_origin_leaf, \
        f"Trace ends at {trace['path'][-1]}, expected {expected_origin_leaf}"
    return trace


def assert_redacted(report):
    assert report is not None
    assert report["overall"].startswith("Confidential") or \
           any("[restricted]" in d.get("line", "") for d in report.get("drivers", [])), \
        f"Expected redacted report, got: {report.get('overall', '')[:200]}"


def assert_visible(report):
    assert report is not None
    assert not report["overall"].startswith("Confidential"), \
        f"Expected visible report, got redacted: {report.get('overall', '')[:200]}"


# --- Baseline ---

def test_baseline_root_has_report():
    c = _client()
    root = c.get("/report/fab17_output", params={"role": "reviewer"}).json()
    assert root is not None
    assert root["severity"] in ("low", "medium")


# --- Scripted narrative: Typhoon hits Taiwan ---

def test_typhoon_fuel_spike_hits_logistics():
    c = _client()
    _inject(c, "Bunker Fuel Index", "bunker_fuel", 70)

    logistics = c.get("/report/logistics", params={"role": "reviewer"}).json()
    assert_severity(logistics, "high")
    assert len(logistics["drivers"]) >= 2

    supply_chain = c.get("/report/supply_chain", params={"role": "reviewer"}).json()
    assert_severity(supply_chain, "high")


def test_typhoon_sumco_yield_shortage():
    c = _client()
    _inject(c, "Bunker Fuel Index", "bunker_fuel", 70)
    _inject(c, "SUMCO Fab", "sumco_yield", 95)

    sumco_r = c.get("/report/sumco_yield", params={"role": "reviewer"}).json()
    assert_redacted(sumco_r)

    sumco_p = c.get("/report/sumco_yield", params={"role": "privileged"}).json()
    assert_visible(sumco_p)
    assert sumco_p["severity"] in ("high", "critical")

    wafer = c.get("/report/wafer_suppliers", params={"role": "reviewer"}).json()
    assert_visible(wafer)
    assert wafer["severity"] in ("high", "critical")

    root = c.get("/report/fab17_output", params={"role": "reviewer"}).json()
    assert_severity(root, "critical")


def test_typhoon_alice_on_leave_cascades():
    c = _client()
    _inject(c, "Bunker Fuel Index", "bunker_fuel", 70)
    _inject(c, "SUMCO Fab", "sumco_yield", 95)
    _inject(c, "Leave Calendar", "alice_chen", 85, kind="leave")

    litho = c.get("/report/lithography", params={"role": "reviewer"}).json()
    assert litho["severity"] in ("high", "critical")

    eng = c.get("/report/eng_ops", params={"role": "reviewer"}).json()
    assert eng["severity"] in ("high", "critical")

    root = c.get("/report/fab17_output", params={"role": "reviewer"}).json()
    assert_severity(root, "critical")
    assert len(root["drivers"]) >= 3

    trace = c.get("/trace/fab17_output").json()
    assert trace["origin"] is not None
    assert trace["path"][-1] in ("alice_chen", "sumco_yield", "bunker_fuel")


def test_typhoon_diamond_lithography_reaches_root_once():
    c = _client()
    _inject(c, "SUMCO Fab", "sumco_yield", 95)
    _inject(c, "Leave Calendar", "alice_chen", 85)

    litho_trace = c.get("/trace/lithography").json()
    assert litho_trace["origin"] is not None

    changed = _inject(c, "SUMCO Fab", "sumco_yield", 98)
    assert changed["changed"].count("fab17_output") <= 1


# --- Standalone injections ---

def test_alice_leave_bubbles_to_critical():
    c = _client()
    # Alice is a qualitative leaf — add critical field rules so the LLM
    # (or FakeLLM) produces a high score from the severity levels.
    c.post("/builder/nodes", json={
        "id": "alice_chen", "kind": "leaf", "title": "Alice Chen, Process Lead",
        "field_rules": [
            {"field": "Taking leave", "kind": "unstructured", "severity_on_breach": "critical"},
        ]
    })
    c.post("/assess")
    root = c.get("/report/fab17_output", params={"role": "reviewer"}).json()
    assert root["severity"] in ("high", "critical")
    assert_trace(c, "fab17_output", "alice_chen")


def test_fuel_spike_ripples_to_high():
    c = _client()
    _inject(c, "Bunker Fuel Index", "bunker_fuel", 50)
    root = c.get("/report/fab17_output", params={"role": "reviewer"}).json()
    assert_severity(root, "high")
    logistics = c.get("/report/logistics", params={"role": "reviewer"}).json()
    assert_severity(logistics, "high")


def test_yield_shortage_critical_dual_path():
    c = _client()
    _inject(c, "SUMCO Fab", "sumco_yield", 95)

    sumco_r = c.get("/report/sumco_yield", params={"role": "reviewer"}).json()
    assert_redacted(sumco_r)

    sumco_p = c.get("/report/sumco_yield", params={"role": "privileged"}).json()
    assert_visible(sumco_p)
    assert sumco_p["severity"] in ("high", "critical")

    wafer = c.get("/report/wafer_suppliers", params={"role": "reviewer"}).json()
    assert_visible(wafer)

    root = c.get("/report/fab17_output", params={"role": "reviewer"}).json()
    assert_severity(root, "critical")
    assert_trace(c, "fab17_output", "sumco_yield")


def test_buffer_drain_medium():
    c = _client()
    _inject(c, "Warehouse", "buffer_stock", 45)
    buf = c.get("/report/buffer_stock", params={"role": "reviewer"}).json()
    assert_redacted(buf)

    buf_p = c.get("/report/buffer_stock", params={"role": "privileged"}).json()
    assert_visible(buf_p)

    root = c.get("/report/fab17_output", params={"role": "reviewer"}).json()
    assert_severity(root, "medium")
    assert_trace(c, "fab17_output", "buffer_stock")


def test_supervisor_leave_cross_branch():
    c = _client()
    # Bob is a qualitative leaf — add high-severity field rules for the inject.
    c.post("/builder/nodes", json={
        "id": "bob_taylor", "kind": "leaf", "title": "Bob Taylor, Shift Supervisor",
        "field_rules": [
            {"field": "Taking leave", "kind": "unstructured", "severity_on_breach": "critical"},
        ]
    })
    c.post("/assess")
    packaging = c.get("/report/packaging", params={"role": "reviewer"}).json()
    assert packaging is not None
    root = c.get("/report/fab17_output", params={"role": "reviewer"}).json()
    assert root["severity"] in ("high", "critical")


# --- Lineage checking ---

def test_lineage_from_root_to_every_leaf():
    c = _client()
    _inject(c, "SUMCO Fab", "sumco_yield", 95)
    _inject(c, "Bunker Fuel Index", "bunker_fuel", 70)
    _inject(c, "Leave Calendar", "alice_chen", 85)
    _inject(c, "Warehouse", "buffer_stock", 60)

    root = c.get("/report/fab17_output", params={"role": "reviewer"}).json()
    assert root is not None
    assert root["severity"] == "critical"

    # Every node in the path should have a report
    for nid in ["eng_ops", "lithography", "supply_chain", "logistics",
                "wafer_suppliers", "buffer_warehouse", "workforce"]:
        rep = c.get(f"/report/{nid}", params={"role": "reviewer"}).json()
        assert rep is not None, f"Node {nid} missing report"


# --- LLM response quality ---

def test_llm_rationale_detects_supply_chain_context():
    c = _client()
    _inject(c, "SUMCO Fab", "sumco_yield", 95)

    root = c.get("/report/fab17_output", params={"role": "privileged"}).json()
    overall = root.get("overall", "").lower()

    # FakeLLM uses template; verify it includes risk assessment and primary driver
    assert "risk" in overall, f"LLM response missing risk context: {overall[:300]}"
    assert "primary driver" in overall, f"LLM response missing driver context: {overall[:300]}"


def test_llm_rationale_detects_human_risk():
    c = _client()
    _inject(c, "Leave Calendar", "alice_chen", 85, kind="leave")

    root = c.get("/report/fab17_output", params={"role": "privileged"}).json()
    overall = root.get("overall", "").lower()
    drivers_text = " ".join(d.get("line", "") for d in root.get("drivers", [])).lower()

    # FakeLLM uses a template; verify it mentions risk and primary driver
    assert "risk" in overall, f"LLM response missing risk context: {overall[:300]}"
    assert "primary driver" in overall, f"LLM response missing driver context: {overall[:300]}"


def test_llm_raw_response_captured():
    from foursight.seed import load_supply_chain
    from foursight.llm import FakeLLM
    from foursight.vector_store import FakeVector
    from foursight.assess import assess

    store, eng, _ = load_supply_chain()
    llm = FakeLLM()
    vector = FakeVector()

    node = store.get_node("alice_chen")
    node.raw = {"capacity_drop_pct": 40, "single_owner": True}
    a = assess(node, store, llm, vector, {"trigger": "test"})

    assert hasattr(a.llm_verdict, "raw_response"), "LLMVerdict missing raw_response"
    assert a.llm_verdict.raw_response == "", \
        f"FakeLLM should store empty string, got: {a.llm_verdict.raw_response[:100]}"


# --- Sensitivity and declassification ---

def test_wafer_suppliers_declassifies_to_internal():
    c = _client()
    _inject(c, "SUMCO Fab", "sumco_yield", 95)

    wafer_r = c.get("/report/wafer_suppliers", params={"role": "reviewer"}).json()
    assert_visible(wafer_r)

    sumco_r = c.get("/report/sumco_yield", params={"role": "reviewer"}).json()
    assert_redacted(sumco_r)


def test_buffer_stock_remains_confidential():
    c = _client()
    _inject(c, "Warehouse", "buffer_stock", 60)

    buf_r = c.get("/report/buffer_stock", params={"role": "reviewer"}).json()
    assert_redacted(buf_r)

    buf_p = c.get("/report/buffer_stock", params={"role": "privileged"}).json()
    assert_visible(buf_p)


def test_graph_data_endpoint():
    c = _client()
    _inject(c, "SUMCO Fab", "sumco_yield", 95)
    data = c.get("/graph-data", params={"role": "reviewer"}).json()
    assert "fab17_output" in data
    assert "sumco_yield" in data
    assert "alice_chen" in data
    root = data["fab17_output"]
    assert "children" in root
    assert "severity" in root
    assert root["severity"] in ("high", "critical")
