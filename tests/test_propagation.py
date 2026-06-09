from datetime import datetime, timezone
from foursight.models import ChangeEvent, Sensitivity, TriggerType
from foursight.propagation import Engine


def _noop(node, store, llm):
    return None


def _change(score):
    return ChangeEvent(source="t", record_ref="r", after={"effect_score": score},
                       at=datetime.now(timezone.utc), sensitivity=Sensitivity.INTERNAL)


def test_propagates_to_root_once(diamond_store, llm, vector):
    eng = Engine(diamond_store, llm, vector, _noop)
    eng.on_data_change("leaf", _change(90))
    changed = eng.fire_node("leaf")
    assert "root" in changed
    assert diamond_store.get_node("root").current is not None


def test_small_change_propagates(diamond_store, llm, vector):
    """With EPSILON removed, even small changes propagate (demo mode)."""
    eng = Engine(diamond_store, llm, vector, _noop)
    eng.on_data_change("leaf", _change(0))
    eng.fire_node("leaf")
    eng.on_data_change("leaf", _change(1))
    changed = eng.fire_node("leaf")
    assert "leaf" in changed
