from foursight.seed import build_seed


def test_seed_dag_and_baseline():
    store, eng, _ = build_seed()
    # Diamond: platform_team has two parents (customer_portal, payments)
    assert any(len(store.parents(nid)) >= 2 for nid in store.all_ids())
    # Cross-branch dependency: alice_owner -> payments_team
    assert any(store.dependencies(nid) for nid in store.all_ids())
    eng.run_full()
    assert store.get_node("root").report is not None


def test_load_supply_chain_seeds_metrics():
    import sqlite3
    from foursight.db import init_db, read_metrics
    from foursight.seed import load_supply_chain
    from foursight.llm import FakeLLM
    from foursight.vector_store import FakeVector
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    store, eng, _ = load_supply_chain(llm=FakeLLM(), vector=FakeVector(), conn=conn)
    assert read_metrics(conn, "sumco_yield")["yield_pct"] == 92.0
    assert store.get_node("sumco_yield").data_binding.raw_values["yield_pct"] == 92.0
    # healthy baseline -> root is not in a breach state
    assert store.get_node("fab17_output").current.llm_verdict.severity.value in ("low", "medium")
