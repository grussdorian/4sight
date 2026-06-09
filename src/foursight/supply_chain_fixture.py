from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from .models import Node, NodeKind, EdgeType, DataBinding, Sensitivity, Severity, FieldRule

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
            field_rules = []
            raw_frs = n.get("field_rules", [])
            if raw_frs:
                field_rules = [
                    FieldRule(
                        field=fr["field"],
                        kind=fr.get("kind", "structured"),
                        operator=fr.get("operator", "<"),
                        expected=float(fr.get("expected", 0)),
                        severity_on_breach=Severity(fr.get("severity_on_breach", "medium")),
                    )
                    for fr in raw_frs
                ]
            else:
                field_rules = [
                    FieldRule(field="effect_score", kind="structured", operator=">=",
                              expected=75.0, severity_on_breach=Severity.CRITICAL),
                    FieldRule(field="effect_score", kind="structured", operator=">=",
                              expected=50.0, severity_on_breach=Severity.HIGH),
                    FieldRule(field="effect_score", kind="structured", operator=">=",
                              expected=25.0, severity_on_breach=Severity.MEDIUM),
                    FieldRule(field="capacity_drop_pct", kind="structured", operator=">",
                              expected=50.0, severity_on_breach=Severity.HIGH),
                    FieldRule(field="single_owner", kind="structured", operator="==",
                              expected=1.0, severity_on_breach=Severity.CRITICAL),
                ]
            binding = DataBinding(adapter_id=n["id"],
                                  sensitivity=Sensitivity(n.get("sensitivity", "internal")),
                                  field_rules=field_rules)
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
