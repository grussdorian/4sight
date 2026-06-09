import pytest
from foursight.models import Node, NodeKind, EdgeType
from foursight.graph_store import GraphStore


def _n(i):
    return Node(id=i, kind=NodeKind.TASK, title=i)


def test_children_parents_and_shared_child():
    s = GraphStore()
    for i in ["T", "t3", "t4", "t1"]:
        s.add_node(_n(i))
    s.add_edge("T", "t3", EdgeType.DECOMPOSITION)
    s.add_edge("T", "t4", EdgeType.DECOMPOSITION)
    s.add_edge("t3", "t1", EdgeType.DECOMPOSITION)
    s.add_edge("t4", "t1", EdgeType.DECOMPOSITION)
    assert set(s.parents("t1")) == {"t3", "t4"}


def test_closure_topo_and_dependency():
    s = GraphStore()
    for i in ["root", "a", "b", "leaf"]:
        s.add_node(_n(i))
    s.add_edge("root", "a", EdgeType.DECOMPOSITION)
    s.add_edge("a", "leaf", EdgeType.DECOMPOSITION)
    s.add_edge("leaf", "b", EdgeType.DEPENDENCY)
    s.add_edge("root", "b", EdgeType.DECOMPOSITION)
    closure = s.closure(["leaf"])
    assert closure == {"leaf", "a", "b", "root"}
    order = s.topo_order(closure)
    assert order.index("leaf") < order.index("a") < order.index("root")


def test_cycle_rejected():
    s = GraphStore()
    for i in ["x", "y"]:
        s.add_node(_n(i))
    s.add_edge("x", "y", EdgeType.DEPENDENCY)
    with pytest.raises(ValueError):
        s.add_edge("y", "x", EdgeType.DEPENDENCY)


def test_influence_successors():
    from foursight.models import Node, NodeKind, EdgeType
    from foursight.graph_store import GraphStore
    s = GraphStore()
    for nid in ["leaf", "root"]:
        s.add_node(Node(id=nid, kind=NodeKind.TASK, title=nid))
    s.add_edge("leaf", "root", EdgeType.DEPENDENCY)  # influence leaf -> root
    assert s.influence_successors("leaf") == ["root"]
    assert s.influence_predecessors("root") == ["leaf"]
