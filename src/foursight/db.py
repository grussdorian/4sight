from __future__ import annotations
import json
import sqlite3
from .models import Node, NodeKind, EdgeType, Severity, Sensitivity, FieldRule, DataBinding
from .graph_store import GraphStore


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS nodes (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
            description TEXT DEFAULT '', delta_accumulator REAL DEFAULT 0.0,
            field_rules_json TEXT DEFAULT '[]',
            raw_values_json TEXT DEFAULT '{}',
            query TEXT DEFAULT '',
            sensitivity TEXT DEFAULT 'internal'
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
        CREATE TABLE IF NOT EXISTS leaf_metrics (
            node_id TEXT NOT NULL, field TEXT NOT NULL,
            value REAL, updated_at TEXT,
            PRIMARY KEY (node_id, field)
        );
        CREATE TABLE IF NOT EXISTS ferry_prices (
            route TEXT PRIMARY KEY, price REAL, updated_at TEXT
        );
    """)
    conn.commit()


def seed_ferry_prices(conn: sqlite3.Connection) -> None:
    """Demo data source for a live-added 'Ferry price' dependency. Seeded with a
    spike (baseline ~28) so adding the node surfaces a Taipei Freight risk."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT OR IGNORE INTO ferry_prices(route, price, updated_at) "
                 "VALUES('taipei', 75.0, ?)", (now,))
    conn.commit()


def seed_metrics(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    """Insert healthy baseline metric rows. INSERT OR IGNORE so existing
    (possibly injected) values are never overwritten on reboot."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    for node_id, field, value in rows:
        conn.execute(
            "INSERT OR IGNORE INTO leaf_metrics(node_id, field, value, updated_at) "
            "VALUES(?,?,?,?)",
            (node_id, field, float(value), now))
    conn.commit()


def read_metrics(conn: sqlite3.Connection, node_id: str) -> dict[str, float]:
    rows = conn.execute(
        "SELECT field, value FROM leaf_metrics WHERE node_id=?", (node_id,)
    ).fetchall()
    return {field: float(value) for field, value in rows if value is not None}


def set_metric(conn: sqlite3.Connection, node_id: str, field: str, value: float) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO leaf_metrics(node_id, field, value, updated_at) "
        "VALUES(?,?,?,?)",
        (node_id, field, float(value), now))
    conn.commit()


def save_graph(store: GraphStore, conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM nodes")
    conn.execute("DELETE FROM edges")
    for nid, node in store.nodes.items():
        frs_json = json.dumps([fr.model_dump(mode="json") for fr in (node.data_binding.field_rules if node.data_binding else [])])
        rvs_json = json.dumps(node.data_binding.raw_values if node.data_binding else {})
        query = node.data_binding.query if node.data_binding else ""
        sensitivity = node.data_binding.sensitivity.value if node.data_binding else "internal"
        conn.execute(
            "INSERT OR REPLACE INTO nodes(id, kind, title, description, "
            "delta_accumulator, field_rules_json, raw_values_json, query, sensitivity) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (node.id, node.kind.value, node.title, node.description,
             node.delta_accumulator, frs_json, rvs_json, query, sensitivity))
    for e in store._edges:
        conn.execute("INSERT OR REPLACE INTO edges(src, dst, type, weight) VALUES(?,?,?,?)",
                     (e.src, e.dst, e.type.value, e.weight.value))
    conn.commit()


def load_graph(conn: sqlite3.Connection) -> GraphStore:
    store = GraphStore()
    rows = conn.execute(
        "SELECT id, kind, title, description, delta_accumulator, "
        "field_rules_json, raw_values_json, query, sensitivity FROM nodes"
    ).fetchall()
    for row in rows:
        nid, kind_str, title, desc, accumulator, frs_json, rvs_json, query, sens_str = row
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
            try:
                sensitivity = Sensitivity(sens_str) if sens_str else Sensitivity.INTERNAL
            except ValueError:
                sensitivity = Sensitivity.INTERNAL
            binding = DataBinding(adapter_id=nid, query=query or "",
                                  sensitivity=sensitivity,
                                  field_rules=field_rules, raw_values=rvs_data)
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
