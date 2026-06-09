from foursight.models import (
    Node, NodeKind, Edge, EdgeType, Severity, Sensitivity,
    severity_from_score, Viewer, Role, FieldRule, Signal, DataBinding,
)


def test_severity_thresholds():
    assert severity_from_score(10) == Severity.LOW
    assert severity_from_score(40) == Severity.MEDIUM
    assert severity_from_score(70) == Severity.HIGH
    assert severity_from_score(90) == Severity.CRITICAL


def test_node_defaults():
    n = Node(id="t1", kind=NodeKind.TASK, title="Task 1")
    assert n.current is None and n.history == [] and n.pending_change is None


def test_viewer_defaults():
    assert Viewer(id="u1", role=Role.REVIEWER).clearances == set()


# --- FieldRule tests ---

def test_field_rule_defaults():
    fr = FieldRule(field="salary_variance")
    assert fr.field == "salary_variance"
    assert fr.kind == "structured"
    assert fr.operator == "<"
    assert fr.expected == 0.0
    assert fr.severity_on_breach == Severity.MEDIUM


def test_field_rule_unstructured():
    fr = FieldRule(field="notes", kind="unstructured", severity_on_breach=Severity.HIGH)
    assert fr.kind == "unstructured"
    assert fr.severity_on_breach == Severity.HIGH


# --- Signal tests ---

def test_signal_creation():
    s = Signal(source_node="n1", score=72.0, severity=Severity.HIGH,
               cause="salary variance 6%", sensitivity=Sensitivity.INTERNAL)
    assert s.source_node == "n1"
    assert s.score == 72.0
    assert s.severity == Severity.HIGH
    assert s.cause == "salary variance 6%"


def test_signal_defaults():
    s = Signal(source_node="n1", score=0.0, severity=Severity.LOW)
    assert s.cause == ""
    assert s.sensitivity == Sensitivity.INTERNAL


# --- Edge tests ---

def test_edge_default_weight():
    e = Edge(src="a", dst="b", type=EdgeType.DEPENDENCY)
    assert e.weight == Severity.MEDIUM


def test_edge_explicit_weight():
    e = Edge(src="a", dst="b", type=EdgeType.DECOMPOSITION, weight=Severity.CRITICAL)
    assert e.weight == Severity.CRITICAL


# --- DataBinding tests ---

def test_data_binding_field_rules_default():
    db = DataBinding(adapter_id="csv")
    assert db.field_rules == []
    assert db.raw_values == {}


def test_data_binding_with_field_rules():
    fr = FieldRule(field="x", severity_on_breach=Severity.HIGH)
    db = DataBinding(adapter_id="csv", field_rules=[fr], raw_values={"x": 42.0})
    assert len(db.field_rules) == 1
    assert db.raw_values["x"] == 42.0


# --- Node signal fields ---

def test_node_has_signal_fields():
    n = Node(id="t1", kind=NodeKind.TASK, title="Task 1")
    assert n.inbound_signals == []
    assert n.outbound_signal is None


def test_node_no_trigger_threshold():
    n = Node(id="t1", kind=NodeKind.TASK, title="Task 1")
    assert not hasattr(n, "trigger_threshold")


def test_node_context_summary_default():
    n = Node(id="t", kind=NodeKind.TASK, title="T")
    assert n.context_summary == ""
