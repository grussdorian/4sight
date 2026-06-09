from datetime import datetime, timezone
from foursight.models import (
    Node, NodeKind, Assessment, LLMVerdict, Severity,
    DataBinding, FieldRule, Signal,
)
from foursight.rules import score_node, score_leaf, RULE_VERSION


def _assessed(nid, score, severity=Severity.HIGH):
    signal = Signal(source_node=nid, score=score, severity=severity, cause="test")
    return Assessment(node_id=nid, version=1, computed_at=datetime.now(timezone.utc),
                      rule_score=score, rule_version=RULE_VERSION,
                      llm_verdict=LLMVerdict(final_score=score, severity=severity, rationale="x"),
                      signal=signal)


def test_leaf_no_field_rules():
    leaf = Node(id="l", kind=NodeKind.LEAF, title="leaf",
                data_binding=DataBinding(adapter_id="csv"))
    r = score_node(leaf, [], [])
    assert r.score == 0.0
    assert r.inputs["reason"] == "no field rules configured"


def test_leaf_structured_breach():
    fr = FieldRule(field="salary_variance", kind="structured", operator="<",
                   expected=5.0, severity_on_breach=Severity.HIGH)
    db = DataBinding(adapter_id="csv", field_rules=[fr], raw_values={"salary_variance": 3.0})
    leaf = Node(id="l", kind=NodeKind.LEAF, title="leaf", data_binding=db)
    r = score_node(leaf, [], [])
    assert r.score == 62.0  # HIGH severity score
    assert len(r.inputs["breached"]) == 1
    assert r.inputs["breached"][0]["field"] == "salary_variance"


def test_leaf_structured_no_breach():
    fr = FieldRule(field="salary_variance", kind="structured", operator="<",
                   expected=5.0, severity_on_breach=Severity.HIGH)
    db = DataBinding(adapter_id="csv", field_rules=[fr], raw_values={"salary_variance": 10.0})
    leaf = Node(id="l", kind=NodeKind.LEAF, title="leaf", data_binding=db)
    r = score_node(leaf, [], [])
    assert r.score == 0.0
    assert len(r.inputs["breached"]) == 0


def test_leaf_multiple_fields_max_severity():
    fr1 = FieldRule(field="a", kind="structured", operator="<",
                    expected=5.0, severity_on_breach=Severity.MEDIUM)
    fr2 = FieldRule(field="b", kind="structured", operator=">",
                    expected=10.0, severity_on_breach=Severity.CRITICAL)
    db = DataBinding(adapter_id="csv", field_rules=[fr1, fr2],
                     raw_values={"a": 2.0, "b": 15.0})
    leaf = Node(id="l", kind=NodeKind.LEAF, title="leaf", data_binding=db)
    r = score_node(leaf, [], [])
    assert r.score == 88.0  # CRITICAL severity score
    assert len(r.inputs["breached"]) == 2


def test_leaf_unstructured_skipped_by_rules():
    fr = FieldRule(field="notes", kind="unstructured",
                   severity_on_breach=Severity.MEDIUM)
    db = DataBinding(adapter_id="csv", field_rules=[fr])
    leaf = Node(id="l", kind=NodeKind.LEAF, title="leaf", data_binding=db)
    r = score_node(leaf, [], [])
    assert r.score == 0.0
    assert "unstructured_fields" in r.inputs


def test_task_with_upstream_signals():
    r = score_node(Node(id="t", kind=NodeKind.TASK, title="t"),
                   [_assessed("c1", 30), _assessed("c2", 80)], [])
    assert r.score == 80.0
    assert r.inputs["contributors"] == 2


def test_task_no_upstream_signals():
    r = score_node(Node(id="t", kind=NodeKind.TASK, title="t"), [], [])
    assert r.score == 0.0
    assert r.inputs["reason"] == "no upstream signals"


def test_severity_to_score_consistency():
    from foursight.rules import _severity_to_score
    scores = {
        Severity.LOW: _severity_to_score(Severity.LOW),
        Severity.MEDIUM: _severity_to_score(Severity.MEDIUM),
        Severity.HIGH: _severity_to_score(Severity.HIGH),
        Severity.CRITICAL: _severity_to_score(Severity.CRITICAL),
    }
    assert scores[Severity.LOW] < scores[Severity.MEDIUM] < scores[Severity.HIGH] < scores[Severity.CRITICAL]
