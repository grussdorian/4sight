from __future__ import annotations
from typing import Callable
from .models import ChangeEvent, TriggerType
from .graph_store import GraphStore
from .assess import assess


class Engine:
    def __init__(self, store: GraphStore, llm, vector, report_fn: Callable) -> None:
        self.store = store
        self.llm = llm
        self.vector = vector
        self.report_fn = report_fn
        self.listeners: list[Callable[[list[str]], None]] = []

    def on_data_change(self, node_id: str, change: ChangeEvent) -> None:
        node = self.store.get_node(node_id)
        node.raw = change.after if isinstance(change.after, dict) else {"effect_score": change.after}
        if node.data_binding and isinstance(change.after, dict):
            for k, v in change.after.items():
                if isinstance(v, (int, float)):
                    node.data_binding.raw_values[k] = float(v)
        node.pending_change = change
        node.pending_delta += 1.0

    def run_crawl(self, scope, trigger):
        order = self.store.topo_order(self.store.closure(scope))
        active = set(scope)
        changed = []
        for nid in order:
            node = self.store.get_node(nid)
            preds = self.store.influence_predecessors(nid)
            if nid not in active and not any(p in active for p in preds):
                continue
            from_pred = next((p for p in preds if p in active), None)
            edge = None
            if from_pred is not None:
                edge = "decomposition" if from_pred in self.store.children(nid) else "dependency"

            a = assess(node, self.store, self.llm, self.vector,
                       {"trigger": trigger.value, "edge": edge, "from": from_pred})

            # Propagate if this node is in scope or has a new outbound signal
            if nid in scope or (node.outbound_signal is not None):
                active.add(nid)
                changed.append(nid)

        for nid in changed:
            self.report_fn(self.store.get_node(nid), self.store, self.llm)
        for nid in scope:
            n = self.store.get_node(nid)
            n.pending_change = None
            n.pending_delta = 0.0
        for listener in self.listeners:
            listener(changed)
        return changed

    def fire_node(self, node_id, trigger=TriggerType.NODE_FIRED):
        return self.run_crawl([node_id], trigger)

    def run_full(self, trigger=TriggerType.TIMEOUT):
        return self.run_crawl(self.store.all_ids(), trigger)
