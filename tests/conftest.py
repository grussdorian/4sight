import pytest
from foursight.models import Node, NodeKind, EdgeType, DataBinding, FieldRule, Severity
from foursight.graph_store import GraphStore
from foursight.llm import FakeLLM
from foursight.vector_store import FakeVector


@pytest.fixture
def llm(): return FakeLLM()


@pytest.fixture
def vector(): return FakeVector()


@pytest.fixture
def diamond_store():
    s = GraphStore()
    s.add_node(Node(id="root", kind=NodeKind.TASK, title="Root"))
    s.add_node(Node(id="a", kind=NodeKind.TASK, title="A"))
    s.add_node(Node(id="b", kind=NodeKind.TASK, title="B"))
    s.add_node(Node(id="leaf", kind=NodeKind.LEAF, title="Leaf",
                    data_binding=DataBinding(adapter_id="leaf", field_rules=[
                        FieldRule(field="effect_score", kind="structured", operator=">=",
                                  expected=75.0, severity_on_breach=Severity.CRITICAL),
                        FieldRule(field="effect_score", kind="structured", operator=">=",
                                  expected=50.0, severity_on_breach=Severity.HIGH),
                        FieldRule(field="effect_score", kind="structured", operator=">=",
                                  expected=25.0, severity_on_breach=Severity.MEDIUM),
                        FieldRule(field="capacity_drop_pct", kind="structured", operator=">=",
                                  expected=50.0, severity_on_breach=Severity.HIGH),
                        FieldRule(field="single_owner", kind="structured", operator="==",
                                  expected=1.0, severity_on_breach=Severity.CRITICAL),
                    ])))
    s.add_edge("root", "a", EdgeType.DECOMPOSITION)
    s.add_edge("root", "b", EdgeType.DECOMPOSITION)
    s.add_edge("a", "leaf", EdgeType.DECOMPOSITION)
    s.add_edge("b", "leaf", EdgeType.DECOMPOSITION)
    return s
