import pytest
from datetime import datetime, timezone
from foursight.models import ChangeEvent, Sensitivity
from foursight.propagation import Engine
from foursight.llm import FakeLLM
from foursight.vector_store import FakeVector
from foursight.testkit import random_dag


def _noop(node, store, llm): return None


@pytest.mark.parametrize("seed", range(10))
def test_crawl_invariants(seed):
    store, leaves = random_dag(seed=seed)
    eng = Engine(store, FakeLLM(), FakeVector(), _noop)
    eng.run_full()                                   # baseline
    origin = leaves[seed % len(leaves)]
    eng.on_data_change(origin, ChangeEvent(source="t", record_ref="r", after={"effect_score": 95},
                       at=datetime.now(timezone.utc), sensitivity=Sensitivity.INTERNAL))
    changed = eng.fire_node(origin)
    assert len(changed) == len(set(changed))          # each node assessed at most once per crawl
    # With EPSILON removed, re-fire propagates through full cone.
    # All nodes with signals will be visited.
    again = eng.fire_node(origin)
    assert origin in again                            # origin always in result
    assert len(again) <= len(changed)                 # no more nodes than first run
