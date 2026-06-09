import sqlite3
from foursight.db import init_db, save_graph, load_graph
from foursight.models import Node, NodeKind, EdgeType
from foursight.graph_store import GraphStore


def test_save_and_load_roundtrip():
    store = GraphStore()
    store.add_node(Node(id="t1", kind=NodeKind.TASK, title="Task 1", description="A test task"))
    store.add_node(Node(id="l1", kind=NodeKind.LEAF, title="Leaf 1", description="A data source"))
    store.add_edge("t1", "l1", EdgeType.DECOMPOSITION)

    conn = sqlite3.connect(":memory:")
    init_db(conn)
    save_graph(store, conn)
    loaded = load_graph(conn)

    assert "t1" in loaded.nodes
    assert loaded.get_node("t1").description == "A test task"
    assert loaded.children("t1") == ["l1"]
    assert len(loaded.all_ids()) == 2


def test_new_fields_persist():
    store = GraphStore()
    node = Node(id="n1", kind=NodeKind.TASK, title="Node",
                description="desc", delta_accumulator=15.0)
    store.add_node(node)
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    save_graph(store, conn)
    loaded = load_graph(conn)
    n = loaded.get_node("n1")
    assert n.description == "desc"
    assert n.delta_accumulator == 15.0
