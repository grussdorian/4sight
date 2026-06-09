from __future__ import annotations
import sqlite3


class SqlSourceAdapter:
    """Runs a leaf node's real SQL query against the operational data tables
    and returns a {field: value} map. The query is expected to return
    (field, value) rows, e.g.

        SELECT field, value FROM leaf_metrics WHERE node_id = 'sumco_yield'
    """

    def __init__(self, conn: sqlite3.Connection, node_id: str, query: str) -> None:
        self.conn = conn
        self.node_id = node_id
        self.query = query

    def fetch(self) -> dict[str, float]:
        rows = self.conn.execute(self.query).fetchall()
        return {row[0]: float(row[1]) for row in rows if row[1] is not None}
