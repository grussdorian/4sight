from __future__ import annotations
import json
import sqlite3
from .models import Node, NodeKind, EdgeType, Severity, FieldRule, DataBinding
from .graph_store import GraphStore


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS nodes (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
            description TEXT DEFAULT '', delta_accumulator REAL DEFAULT 0.0,
            field_rules_json TEXT DEFAULT '[]',
            raw_values_json TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS edges (
            src TEXT NOT NULL, dst TEXT NOT NULL, type TEXT NOT NULL,
            weight TEXT DEFAULT 'medium',
            PRIMARY KEY (src, dst, type)
        );
        CREATE TABLE IF NOT EXISTS assessments (
            node_id TEXT NOT NULL, version INTEGER NOT NULL,
            raw_json TEXT NOT NULL, PRIMARY KEY (node_id, version)
        );
        CREATE TABLE IF NOT EXISTS reports (
            node_id TEXT PRIMARY KEY, raw_json TEXT NOT NULL
        );
    """)
    conn.commit()


def save_graph(store: GraphStore, conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM nodes")
    conn.execute("DELETE FROM edges")
    for nid, node in store.nodes.items():
        frs_json = json.dumps([fr.model_dump(mode="json") for fr in (node.data_binding.field_rules if node.data_binding else [])])
        rvs_json = json.dumps(node.data_binding.raw_values if node.data_binding else {})
        conn.execute(
            "INSERT OR REPLACE INTO nodes(id, kind, title, description, "
            "delta_accumulator, field_rules_json, raw_values_json) VALUES(?,?,?,?,?,?,?)",
            (node.id, node.kind.value, node.title, node.description,
             node.delta_accumulator, frs_json, rvs_json))
    for e in store._edges:
        conn.execute("INSERT OR REPLACE INTO edges(src, dst, type, weight) VALUES(?,?,?,?)",
                     (e.src, e.dst, e.type.value, e.weight.value))
    conn.commit()


def load_graph(conn: sqlite3.Connection) -> GraphStore:
    store = GraphStore()
    rows = conn.execute(
        "SELECT id, kind, title, description, delta_accumulator, "
        "field_rules_json, raw_values_json FROM nodes"
    ).fetchall()
    for row in rows:
        nid, kind_str, title, desc, accumulator, frs_json, rvs_json = row
        frs_data = json.loads(frs_json) if frs_json else []
        rvs_data = json.loads(rvs_json) if rvs_json else {}
        field_rules = []
        for fr in frs_data:
            field_rules.append(FieldRule(
                field=fr.get("field", ""),
                kind=fr.get("kind", "structured"),
                operator=fr.get("operator", "<"),
                expected=float(fr.get("expected", 0)),
                severity_on_breach=Severity(fr.get("severity_on_breach", "medium")),
            ))
        binding = None
        if kind_str == "leaf":
            binding = DataBinding(adapter_id=nid, field_rules=field_rules, raw_values=rvs_data)
        node = Node(id=nid, kind=NodeKind(kind_str), title=title,
                    description=desc or "",
                    delta_accumulator=accumulator or 0.0,
                    data_binding=binding)
        store.add_node(node)
    for row in conn.execute("SELECT src, dst, type, weight FROM edges").fetchall():
        try:
            w = Severity(row[3]) if row[3] else Severity.MEDIUM
        except ValueError:
            w = Severity.MEDIUM
        store.add_edge(row[0], row[1], EdgeType(row[2]), w)
    return store
