import sqlite3

from foursight.db import init_db, set_metric
from foursight.seed import load_supply_chain
from foursight.poll import PollService
from foursight.triggers import TriggerEngine
from foursight.llm import FakeLLM
from foursight.vector_store import FakeVector


def _setup():
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    store, eng, _ = load_supply_chain(llm=FakeLLM(), vector=FakeVector(), conn=conn)
    return conn, store, eng


def test_poll_updates_raw_values_and_fires_on_change():
    conn, store, eng = _setup()
    set_metric(conn, "sumco_yield", "yield_pct", 55.0)  # HIGH band
    poller = PollService(store, conn, eng, TriggerEngine(store))
    changed = poller.poll(["sumco_yield"])
    assert "sumco_yield" in changed
    assert store.get_node("sumco_yield").data_binding.raw_values["yield_pct"] == 55.0
    assert store.get_node("sumco_yield").current.llm_verdict.severity.value in ("high", "critical")


def test_poll_no_change_does_not_fire():
    conn, store, eng = _setup()
    poller = PollService(store, conn, eng, TriggerEngine(store))
    changed = poller.poll(["sumco_yield"])  # value unchanged from seeded baseline
    assert changed == []
