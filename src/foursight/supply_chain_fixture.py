from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from .models import Node, NodeKind, EdgeType, DataBinding, Sensitivity, Severity

FIXTURES = Path(__file__).parent / "fixtures" / "supply_chain"


@dataclass
class SupplyChainSpec:
    nodes: list[Node]
    edges: list[tuple[str, str, EdgeType, Severity]]  # added weight
    policy_docs: list[tuple[str, str]] = field(default_factory=list)


def parse_supply_chain(path: str | Path = FIXTURES) -> SupplyChainSpec:
    path = Path(path)
    data = json.loads((path / "topology.json").read_text())
    nodes = []
    for n in data["nodes"]:
        kind = NodeKind(n["kind"])
        binding = None
        if kind == NodeKind.LEAF:
            binding = DataBinding(adapter_id=n["id"],
                                  sensitivity=Sensitivity(n.get("sensitivity", "internal")))
        nodes.append(Node(id=n["id"], kind=kind, title=n["title"],
                          data_binding=binding, raw={} if kind == NodeKind.LEAF else None))
    edges = []
    for e in data["edges"]:
        weight_str = e.get("weight", "medium")
        try:
            weight = Severity(weight_str)
        except ValueError:
            weight = Severity.MEDIUM
        edges.append((e["src"], e["dst"], EdgeType(e.get("type", "dependency")), weight))
    docs = []
    pol = path / "policies"
    if pol.exists():
        for f in sorted(pol.glob("*.md")):
            docs.append((f.stem, f.read_text()))
    return SupplyChainSpec(nodes=nodes, edges=edges, policy_docs=docs)
