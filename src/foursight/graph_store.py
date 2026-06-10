from __future__ import annotations
import hashlib
import json
import networkx as nx
from .models import Node, Edge, EdgeType, Severity


def content_hash(binding) -> str:
    raw = f"{binding.adapter_id}:{binding.query.strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class GraphStore:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self._edges: list[Edge] = []
        self._infl = nx.DiGraph()   # u -> v means "v depends on u"

    def add_node(self, node: Node) -> None:
        self.nodes[node.id] = node
        self._infl.add_node(node.id)

    def get_node(self, node_id: str) -> Node:
        return self.nodes[node_id]

    def _influence_edge(self, edge: Edge) -> tuple[str, str]:
        if edge.type == EdgeType.DECOMPOSITION:
            return (edge.dst, edge.src)
        return (edge.src, edge.dst)

    def add_edge(self, src: str, dst: str, type: EdgeType, weight: Severity | None = None) -> None:
        edge = Edge(src=src, dst=dst, type=type)
        if weight is not None:
            edge.weight = weight
        u, v = self._influence_edge(edge)
        if u == v or nx.has_path(self._infl, v, u):
            raise ValueError(f"edge {src}->{dst} ({type.value}) would create a cycle")
        self._edges.append(edge)
        self._infl.add_edge(u, v)

    def children(self, node_id: str) -> list[str]:
        return [e.dst for e in self._edges if e.src == node_id]

    def parents(self, node_id: str) -> list[str]:
        return [e.src for e in self._edges if e.dst == node_id]

    def dependencies(self, node_id: str) -> list[str]:
        return [e.src for e in self._edges if e.dst == node_id and e.type == EdgeType.DEPENDENCY]

    def dependents(self, node_id: str) -> list[str]:
        return [e.dst for e in self._edges if e.src == node_id and e.type == EdgeType.DEPENDENCY]

    def has_children(self, node_id: str) -> bool:
        return len(self.children(node_id)) > 0

    def influence_predecessors(self, node_id: str) -> list[str]:
        return list(self._infl.predecessors(node_id))

    def influence_successors(self, node_id: str) -> list[str]:
        return list(self._infl.successors(node_id))

    def closure(self, scope: list[str]) -> set[str]:
        """All nodes in the influence cone of `scope` — both downstream
        (nodes that depend on scope via successors) and upstream (nodes
        that scope depends on via predecessors). This covers both edge
        conventions: leaf→task and task→leaf. The crawl only visits
        the relevant subset."""
        seen, stack = set(scope), list(scope)
        while stack:
            n = stack.pop()
            for s in self._infl.successors(n):
                if s not in seen:
                    seen.add(s)
                    stack.append(s)
            for s in self._infl.predecessors(n):
                if s not in seen:
                    seen.add(s)
                    stack.append(s)
        return seen

    def topo_order(self, subset: set[str]) -> list[str]:
        return list(nx.topological_sort(self._infl.subgraph(subset)))

    def all_ids(self) -> list[str]:
        return list(self.nodes.keys())

    def find_duplicate_source(self, binding) -> str | None:
        h = content_hash(binding)
        for nid, node in self.nodes.items():
            if node.data_binding and content_hash(node.data_binding) == h:
                return nid
        return None

    def snapshot(self, path: str) -> None:
        nodes_data = []
        for n in self.nodes.values():
            nd = n.model_dump(mode="json")
            # Remove runtime signal caches from snapshot
            nd["inbound_signals"] = []
            nd["outbound_signal"] = None
            nodes_data.append(nd)
        data = {
            "nodes": nodes_data,
            "edges": [{"src": e.src, "dst": e.dst, "type": e.type.value, "weight": e.weight.value}
                      for e in self._edges],
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
