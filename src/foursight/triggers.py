from __future__ import annotations
from .graph_store import GraphStore


class TriggerEngine:
    def __init__(self, store: GraphStore) -> None:
        self.store = store

    def accumulate(self, node_id: str, delta: float) -> None:
        node = self.store.get_node(node_id)
        node.delta_accumulator += delta

    def check_and_fire(self) -> list[str]:
        fired: list[str] = []
        for nid in self.store.all_ids():
            node = self.store.get_node(nid)
            if node.delta_accumulator > 0:
                fired.append(nid)
                node.delta_accumulator = 0.0
        return fired

    def influence_cone(self, node_ids: list[str]) -> set[str]:
        return self.store.closure(node_ids)
