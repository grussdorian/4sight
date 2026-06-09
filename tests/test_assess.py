from foursight.assess import assess
from foursight.models import TriggerType, DataBinding, FieldRule, Severity


def test_leaf_provenance(diamond_store, llm, vector):
    leaf = diamond_store.get_node("leaf")
    fr = FieldRule(field="capacity_drop_pct", kind="structured", operator=">",
                   expected=0.0, severity_on_breach=Severity.HIGH)
    leaf.data_binding = DataBinding(adapter_id="csv", field_rules=[fr],
                                     raw_values={"capacity_drop_pct": 40.0})
    a = assess(leaf, diamond_store, llm, vector, {"trigger": TriggerType.NODE_FIRED.value})
    assert a.rule_score == 62.0  # HIGH = 62
    assert a.version == 1 and leaf.current is a
    assert a.signal is not None
    assert a.signal.score >= 62.0


def test_task_rollup(diamond_store, llm, vector):
    leaf = diamond_store.get_node("leaf")
    fr = FieldRule(field="x", kind="structured", operator=">",
                   expected=0.0, severity_on_breach=Severity.CRITICAL)
    leaf.data_binding = DataBinding(adapter_id="csv", field_rules=[fr],
                                     raw_values={"x": 90.0})
    assess(leaf, diamond_store, llm, vector, {})
    a = assess(diamond_store.get_node("a"), diamond_store, llm, vector, {})
    assert a.llm_verdict is not None
    assert a.upstream_versions == {"leaf": 1}
