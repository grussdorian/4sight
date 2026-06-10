from fastapi.testclient import TestClient
from foursight.api import build_app
from foursight.seed import build_seed

# These exercise the legacy build_seed company graph (root, personnel_budget,
# Personnel origin) and the simulate-change demo acts, so they pin seed_fn
# explicitly now that build_app defaults to the Fab 17 supply chain.


def test_real_core_leave_demo():
    client = TestClient(build_app(seed_fn=build_seed))
    before = client.get("/report/root", params={"role": "reviewer"}).json()
    client.post("/simulate-change", json={"kind": "leave"})
    after = client.get("/report/root", params={"role": "reviewer"}).json()
    order = ["low", "medium", "high", "critical"]
    assert order.index(after["severity"]) >= order.index(before["severity"])
    assert after["severity"] in ("high", "critical")
    assert "Personnel" in client.get("/trace/root").json()["origin"]["source"]


def test_real_core_salary_effect_only_for_reviewer():
    client = TestClient(build_app(seed_fn=build_seed))
    client.post("/simulate-change", json={"kind": "salary"})
    rep = client.get("/report/personnel_budget", params={"role": "reviewer"}).json()
    assert rep is not None and "salary" not in rep["overall"].lower()
