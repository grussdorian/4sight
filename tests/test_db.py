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


def test_metrics_roundtrip():
    import sqlite3
    from foursight.db import init_db, seed_metrics, read_metrics, set_metric
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    seed_metrics(conn, [("sumco_yield", "yield_pct", 92.0)])
    assert read_metrics(conn, "sumco_yield") == {"yield_pct": 92.0}
    # seed is idempotent (INSERT OR IGNORE) — does not overwrite
    seed_metrics(conn, [("sumco_yield", "yield_pct", 10.0)])
    assert read_metrics(conn, "sumco_yield") == {"yield_pct": 92.0}
    set_metric(conn, "sumco_yield", "yield_pct", 55.0)
    assert read_metrics(conn, "sumco_yield") == {"yield_pct": 55.0}


def test_save_load_preserves_query_and_sensitivity():
    import sqlite3
    from foursight.db import init_db, save_graph, load_graph
    from foursight.graph_store import GraphStore
    from foursight.models import Node, NodeKind, DataBinding, Sensitivity, FieldRule, Severity
    s = GraphStore()
    s.add_node(Node(id="leaf1", kind=NodeKind.LEAF, title="Leaf 1",
                    data_binding=DataBinding(adapter_id="leaf1",
                        query="SELECT field, value FROM leaf_metrics WHERE node_id='leaf1'",
                        sensitivity=Sensitivity.CONFIDENTIAL,
                        field_rules=[FieldRule(field="yield_pct", operator="<", expected=70.0,
                                               severity_on_breach=Severity.HIGH)])))
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    save_graph(s, conn)
    s2 = load_graph(conn)
    binding = s2.get_node("leaf1").data_binding
    assert binding.query.startswith("SELECT field, value")
    assert binding.sensitivity == Sensitivity.CONFIDENTIAL
    assert binding.field_rules[0].field == "yield_pct"


def test_node_history_caps_and_dedups():
    import sqlite3
    from foursight.db import init_db, record_history, read_history
    conn = sqlite3.connect(":memory:")
    init_db(conn)
    # 10 distinct contexts -> keep last 8
    for i in range(10):
        record_history(conn, "pkg", [{"id": "alice", "severity": "low", "score": i}], "low", float(i), "r")
    h = read_history(conn, "pkg")
    assert len(h) == 8
    assert h[-1]["score"] == 9.0  # newest kept, oldest dropped
    # repeating the current context is skipped (only changed contexts stored)
    n = len(read_history(conn, "pkg"))
    record_history(conn, "pkg", [{"id": "alice", "severity": "low", "score": 9}], "low", 9.0, "r")
    assert len(read_history(conn, "pkg")) == n  # unchanged
    # a changed context is recorded
    record_history(conn, "pkg", [{"id": "alice", "severity": "critical", "score": 40}], "critical", 70.0, "r")
    assert read_history(conn, "pkg")[-1]["severity"] == "critical"
