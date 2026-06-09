from __future__ import annotations
import sqlite3

from .models import NodeKind, TriggerType
from .graph_store import GraphStore
from .ingestion.sql_adapter import SqlSourceAdapter
from .triggers import TriggerEngine


class PollService:
    """On-demand polling of leaf data sources.

    For each polled leaf it runs the leaf's real SQL query, writes the result
    into the leaf's raw_values, feeds the change magnitude to the TriggerEngine,
    and runs a crawl over the influence cone of any nodes that fired.
    """

    def __init__(self, store: GraphStore, conn: sqlite3.Connection,
                 engine, triggers: TriggerEngine | None = None) -> None:
        self.store = store
        self.conn = conn
        self.engine = engine
        self.triggers = triggers or TriggerEngine(store)

    def _leaf_ids(self) -> list[str]:
        out = []
        for nid in self.store.all_ids():
            node = self.store.get_node(nid)
            if node.kind == NodeKind.LEAF and node.data_binding and node.data_binding.query:
                out.append(nid)
        return out

    def refresh(self, node_ids: list[str] | None = None) -> list[str]:
        """Fetch each leaf's SQL data into its raw_values WITHOUT assessing.
        Returns the ids whose readings changed. Used by the batch-assessment
        path, where a single flattened LLM call does the scoring afterward."""
        targets = node_ids if node_ids is not None else self._leaf_ids()
        changed = []
        for nid in targets:
            node = self.store.get_node(nid)
            if not (node.data_binding and node.data_binding.query):
                continue
            fetched = SqlSourceAdapter(self.conn, nid, node.data_binding.query).fetch()
            prev = dict(node.data_binding.raw_values)
            node.data_binding.raw_values.update(fetched)
            if any(prev.get(f) != v for f, v in fetched.items()):
                changed.append(nid)
        return changed

    def poll(self, node_ids: list[str] | None = None) -> list[str]:
        targets = node_ids if node_ids is not None else self._leaf_ids()
        for nid in targets:
            node = self.store.get_node(nid)
            if not (node.data_binding and node.data_binding.query):
                continue
            fetched = SqlSourceAdapter(self.conn, nid, node.data_binding.query).fetch()
            prev = dict(node.data_binding.raw_values)
            delta = sum(abs(v - prev.get(f, 0.0)) for f, v in fetched.items())
            node.data_binding.raw_values.update(fetched)
            if delta > 0:
                self.triggers.accumulate(nid, delta)

        fired = self.triggers.check_and_fire()
        if not fired:
            return []
        return self.engine.run_crawl(self.store.closure(fired), TriggerType.NODE_FIRED)
