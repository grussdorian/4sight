from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from .models import Node, NodeKind, EdgeType, DataBinding, Sensitivity, Severity
from .rules import graded_field_rules, generic_effect_score_rules

FIXTURES = Path(__file__).parent / "fixtures" / "supply_chain"


# Per-leaf real domain field, direction, severity bands, and healthy baseline.
# direction "low" => lower is worse; "high" => higher is worse.
LEAF_SPECS: dict[str, dict] = {
    "sumco_yield":   {"field": "yield_pct",   "direction": "low",
                      "bands": {Severity.CRITICAL: 50, Severity.HIGH: 60, Severity.MEDIUM: 70},
                      "baseline": 92.0},
    "tsmc_wafer":    {"field": "supply_pct",  "direction": "low",
                      "bands": {Severity.CRITICAL: 60, Severity.HIGH: 70, Severity.MEDIUM: 80},
                      "baseline": 95.0},
    "gf_wafer":      {"field": "supply_pct",  "direction": "low",
                      "bands": {Severity.CRITICAL: 60, Severity.HIGH: 70, Severity.MEDIUM: 80},
                      "baseline": 90.0},
    "bunker_fuel":   {"field": "fuel_price",  "direction": "high",
                      "bands": {Severity.CRITICAL: 80, Severity.HIGH: 65, Severity.MEDIUM: 50},
                      "baseline": 30.0},
    "buffer_stock":  {"field": "stock_pct",   "direction": "low",
                      "bands": {Severity.CRITICAL: 15, Severity.HIGH: 25, Severity.MEDIUM: 30},
                      "baseline": 70.0},
    "maint_crew":    {"field": "staffing_pct", "direction": "low",
                      "bands": {Severity.CRITICAL: 40, Severity.HIGH: 60},
                      "baseline": 85.0},
}


def leaf_query(node_id: str) -> str:
    return f"SELECT field, value FROM leaf_metrics WHERE node_id = '{node_id}'"


def metric_baselines() -> list[tuple]:
    """Healthy baseline rows for leaf_metrics: (node_id, field, value)."""
    return [(nid, spec["field"], spec["baseline"]) for nid, spec in LEAF_SPECS.items()]


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
            # Parse explicit field_rules from topology JSON first (qualitative leaves).
            raw_frs = n.get("field_rules", [])
            if raw_frs:
                from .models import FieldRule
                explicit_rules = []
                for fr in raw_frs:
                    explicit_rules.append(FieldRule(
                        field=fr["field"],
                        kind=fr.get("kind", "structured"),
                        operator=fr.get("operator", "<"),
                        expected=float(fr.get("expected", 0)),
                        severity_on_breach=Severity(fr.get("severity_on_breach", "medium")),
                    ))
                # Qualitative leaves (only unstructured rules) get NO SQL query.
                # Mixed or structured rules can co-exist with a query.
                query = leaf_query(n["id"]) if n["id"] in LEAF_SPECS else ""
                binding = DataBinding(adapter_id=n["id"],
                                      query=query,
                                      sensitivity=Sensitivity(n.get("sensitivity", "internal")),
                                      field_rules=explicit_rules)
            else:
                spec = LEAF_SPECS.get(n["id"])
                if spec:
                    field_rules = graded_field_rules(
                        spec["field"], spec["direction"], spec["bands"]
                    ) + generic_effect_score_rules()
                else:
                    # Backward compat: convert old threshold_rules if present.
                    raw_trs = n.get("threshold_rules", [])
                    if raw_trs:
                        from .models import FieldRule
                        field_rules = []
                        for tr in raw_trs:
                            op = tr.get("operator", "<")
                            val = float(tr.get("value", 0))
                            sev = Severity.HIGH  # old rules used HIGH implicitly
                            field_rules.append(FieldRule(
                                field=tr["field"], kind="structured",
                                operator=op, expected=val,
                                severity_on_breach=sev))
                    else:
                        field_rules = generic_effect_score_rules()
                binding = DataBinding(adapter_id=n["id"],
                                      query=leaf_query(n["id"]),
                                      sensitivity=Sensitivity(n.get("sensitivity", "internal")),
                                      field_rules=field_rules)
        nodes.append(Node(id=n["id"], kind=kind, title=n["title"],
                          description=n.get("description", ""),
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
