from foursight.flatten import FlattenEngine
from foursight.graph_store import GraphStore
from foursight.models import Node, NodeKind, EdgeType


def _build_diamond():
    s = GraphStore()
    for nid, title, kind in [("root", "Root", NodeKind.TASK), ("a", "A", NodeKind.TASK),
                             ("b", "B", NodeKind.TASK), ("leaf", "Leaf", NodeKind.LEAF)]:
        s.add_node(Node(id=nid, kind=kind, title=title, description=f"Desc {nid}"))
    for src, dst, t in [("root", "a", "decomposition"), ("root", "b", "decomposition"),
                         ("a", "leaf", "decomposition"), ("b", "leaf", "decomposition")]:
        s.add_edge(src, dst, EdgeType(t))
    return s


def test_flatten_full_includes_all_nodes():
    store = _build_diamond()
    eng = FlattenEngine(store)
    prompt = eng.flatten_full()
    for nid in ["root", "a", "b", "leaf"]:
        assert nid in prompt
    # structure is rendered by influence direction (inputs feeding each node)
    assert "Inputs" in prompt or "feeding" in prompt.lower()


def test_flatten_delta_only_changed():
    store = _build_diamond()
    store.get_node("leaf").delta_accumulator = 60.0
    store.get_node("root").delta_accumulator = 10.0
    eng = FlattenEngine(store)
    prompt = eng.flatten_delta()
    assert "leaf" in prompt
    assert "id=a)" not in prompt
    assert "id=root)" in prompt


def test_build_batch_prompt_has_system_context():
    store = _build_diamond()
    eng = FlattenEngine(store)
    system, messages = eng.build_batch_prompt(mode="full")
    assert "operational risk" in system.lower()
    assert len(messages) == 1


def test_parse_batch_response():
    store = _build_diamond()
    eng = FlattenEngine(store)
    raw = '[{"node_id":"root","final_score":85,"severity":"critical","rationale":"test","summary":"Root summary"}]'
    result = eng.parse_batch_response(raw)
    assert len(result) == 1
    assert result[0]["node_id"] == "root"
    assert result[0]["final_score"] == 85
