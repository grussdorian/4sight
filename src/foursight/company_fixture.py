from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from .models import Node, NodeKind, EdgeType, DataBinding, Sensitivity, Severity
from .rules import leaf_field_rules_from_json

FIXTURES = Path(__file__).parent / "fixtures" / "mock_company"

@dataclass
class CompanySpec:
    nodes: list[Node]
    edges: list[tuple[str, str, EdgeType, Severity]]  # added weight
    policy_docs: list[tuple[str, str]] = field(default_factory=list)

def parse_company(path: str | Path = FIXTURES) -> CompanySpec:
    path = Path(path)
    data = json.loads((path / "topology.json").read_text())
    nodes = []
    for n in data["nodes"]:
        kind = NodeKind(n["kind"])
        binding = None
        if kind == NodeKind.LEAF:
            field_rules = leaf_field_rules_from_json(n)
            binding = DataBinding(
                adapter_id=n["id"],
                sensitivity=Sensitivity(n.get("sensitivity", "internal")),
                field_rules=field_rules,
            )
        nodes.append(Node(id=n["id"], kind=kind, title=n["title"],
                          data_binding=binding, raw={} if kind == NodeKind.LEAF else None))

    edges = []
    for e in data["edges"]:
        weight_str = e.get("weight", "medium")
        try:
            weight = Severity(weight_str)
        except ValueError:
            weight = Severity.MEDIUM
        edges.append((e["src"], e["dst"], EdgeType(e["type"]), weight))

    docs = []
    pol = path / "policies"
    if pol.exists():
        for f in sorted(pol.glob("*.md")):
            docs.append((f.stem, f.read_text()))
    return CompanySpec(nodes=nodes, edges=edges, policy_docs=docs)
