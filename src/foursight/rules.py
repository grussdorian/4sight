from __future__ import annotations
from dataclasses import dataclass, field
from .models import Node, NodeKind, Assessment, Severity, FieldRule

RULE_VERSION = "ops-risk-v2"


def generic_effect_score_rules() -> list[FieldRule]:
    """The default field-rule ladder every demo leaf carries. The
    `simulate-change` / `/raw-values` path injects these synthetic fields, so
    keeping them on every leaf is what makes the demo fire regardless of the
    leaf's domain-specific rules."""
    return [
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
        FieldRule(field="data_age_h", kind="structured", operator=">",
                  expected=120.0, severity_on_breach=Severity.MEDIUM),
    ]


def graded_field_rules(field: str, direction: str, bands: dict) -> list[FieldRule]:
    """Build a graded set of FieldRules on one real domain field.

    direction "low"  -> lower is worse, operator "<" (breach when value < threshold)
    direction "high" -> higher is worse, operator ">" (breach when value > threshold)
    bands maps Severity -> threshold value.
    """
    op = "<" if direction == "low" else ">"
    return [
        FieldRule(field=field, kind="structured", operator=op,
                  expected=float(threshold), severity_on_breach=sev)
        for sev, threshold in bands.items()
    ]


def translate_threshold_rules(raw_rules: list[dict]) -> list[FieldRule]:
    """Translate the legacy `threshold_rules` topology format
    ({field, operator, value}) into `FieldRule`s so they are honored rather
    than silently dropped on load. A threshold breach maps to HIGH severity."""
    translated: list[FieldRule] = []
    for tr in raw_rules:
        translated.append(FieldRule(
            field=tr["field"], kind="structured",
            operator=tr.get("operator", "<"),
            expected=float(tr.get("value", tr.get("expected", 0))),
            severity_on_breach=Severity(tr.get("severity_on_breach", "high")),
        ))
    return translated


def leaf_field_rules_from_json(node_json: dict) -> list[FieldRule]:
    """Build a leaf's field rules from a topology node.

    Precedence: explicit `field_rules` win outright. Otherwise translate any
    legacy `threshold_rules` and always append the generic effect_score ladder
    so the simulate-change demo still produces signals.
    """
    explicit = node_json.get("field_rules", [])
    if explicit:
        return [
            FieldRule(
                field=fr["field"],
                kind=fr.get("kind", "structured"),
                operator=fr.get("operator", "<"),
                expected=float(fr.get("expected", 0)),
                severity_on_breach=Severity(fr.get("severity_on_breach", "medium")),
            )
            for fr in explicit
        ]
    return translate_threshold_rules(node_json.get("threshold_rules", [])) + generic_effect_score_rules()


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
            # A qualitative/unstructured rule has no numeric threshold: its
            # PRESENCE asserts the condition is active (e.g. "Alice is on
            # leave"). It contributes its severity directly so human-risk
            # factors score and propagate. Delete the rule to clear it.
            inputs.setdefault("unstructured_fields", []).append(rule.field)
            s = _severity_to_score(rule.severity_on_breach)
            max_score = max(max_score, s)
            breached.append({"field": rule.field, "value": "active",
                             "expected": "asserted", "operator": "is",
                             "severity": rule.severity_on_breach.value})
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
