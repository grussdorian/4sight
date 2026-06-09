from __future__ import annotations
from dataclasses import dataclass, field
from .models import Node, NodeKind, Assessment, Severity

RULE_VERSION = "ops-risk-v2"


@dataclass
class RuleResult:
    score: float
    inputs: dict = field(default_factory=dict)


def _severity_to_score(sev: Severity) -> float:
    """Map severity to a representative numeric score for rule-based checks."""
    return {"low": 12.0, "medium": 37.0, "high": 62.0, "critical": 88.0}[sev.value]


def score_leaf(node: Node) -> RuleResult:
    """Score a leaf node by checking each field rule against raw values."""
    binding = node.data_binding
    if not binding or not binding.field_rules:
        return RuleResult(0.0, {"reason": "no field rules configured"})

    raw = binding.raw_values
    max_score = 0.0
    breached = []
    inputs: dict = {"fields_checked": len(binding.field_rules), "breached": []}

    for rule in binding.field_rules:
        if rule.kind == "unstructured":
            inputs.setdefault("unstructured_fields", []).append(rule.field)
            continue

        val = raw.get(rule.field)
        if val is None:
            continue

        breached_flag = False
        op = rule.operator
        if op == "<" and val < rule.expected:
            breached_flag = True
        elif op == ">" and val > rule.expected:
            breached_flag = True
        elif op == "<=" and val <= rule.expected:
            breached_flag = True
        elif op == ">=" and val >= rule.expected:
            breached_flag = True
        elif op == "==" and val == rule.expected:
            breached_flag = True

        if breached_flag:
            s = _severity_to_score(rule.severity_on_breach)
            max_score = max(max_score, s)
            breached.append({"field": rule.field, "value": val, "expected": rule.expected,
                           "operator": op, "severity": rule.severity_on_breach.value})

    inputs["breached"] = breached
    inputs["max_score"] = max_score
    return RuleResult(max_score, inputs)


def score_node(node: Node, children: list[Assessment], deps: list[Assessment]) -> RuleResult:
    """Score a node. Leaf nodes use field_rules. Non-leaf nodes return a placeholder;
    the LLM handles synthesis in the assess phase."""
    if node.kind == NodeKind.LEAF:
        return score_leaf(node)

    upstream_scores = [a.signal.score for a in (children + deps) if a.signal]
    if not upstream_scores:
        return RuleResult(0.0, {"reason": "no upstream signals"})

    return RuleResult(
        max(upstream_scores),
        {"contributors": len(upstream_scores), "max": max(upstream_scores),
         "all_scores": upstream_scores}
    )
