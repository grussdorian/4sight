from __future__ import annotations
from datetime import datetime, timezone
from .models import Node, Assessment, Signal, Severity, severity_from_score, LLMVerdict, Sensitivity
from .graph_store import GraphStore
from .rules import score_node, RULE_VERSION
from .sensitivity import combine_sensitivity, declassify


def _now(): return datetime.now(timezone.utc)


def _build_leaf_signal(node: Node, rule_score: float, rule_inputs: dict,
                        llm_verdict) -> Signal:
    """Build a Signal from field rule results + LLM verdict on unstructured fields."""
    binding = node.data_binding

    # Compose cause from breached structured fields
    breached = rule_inputs.get("breached", [])
    cause_parts = []
    for b in breached:
        cause_parts.append(
            f"{b['field']} {b['operator']} {b['expected']} "
            f"(actual: {b['value']}, severity: {b['severity']})"
        )

    # Add LLM verdict for unstructured fields
    unstructured_fields = rule_inputs.get("unstructured_fields", [])
    if unstructured_fields and llm_verdict:
        cause_parts.insert(0, llm_verdict.rationale)

    cause = "; ".join(cause_parts) if cause_parts else (
        llm_verdict.rationale if llm_verdict else "no breached fields"
    )

    final_score = max(rule_score, llm_verdict.final_score if llm_verdict else 0.0)
    severity = severity_from_score(final_score)

    sensitivity_val = binding.sensitivity if binding else Sensitivity.INTERNAL
    min_disc = binding.min_disclosure if binding else Sensitivity.INTERNAL

    return Signal(
        source_node=node.id,
        score=final_score,
        cause=cause,
        severity=severity,
        sensitivity=min_disc,
    )


def _build_nonleaf_signal(node: Node, llm_verdict, sensitivity_val) -> Signal | None:
    """Build a Signal from the LLM's synthesis of upstream signals."""
    if llm_verdict is None:
        return None

    return Signal(
        source_node=node.id,
        score=llm_verdict.final_score,
        cause=llm_verdict.rationale,
        severity=llm_verdict.severity,
        sensitivity=sensitivity_val,
    )


def redact_cause(signal: Signal) -> str:
    """Cause text is clearance-gated. INTERNAL is the baseline disclosure level,
    so only CONFIDENTIAL and RESTRICTED causes are redacted; INTERNAL/PUBLIC
    causes flow through to the synthesizing LLM (and to reviewers)."""
    if signal.sensitivity in (Sensitivity.CONFIDENTIAL, Sensitivity.RESTRICTED):
        return f"[REDACTED - {signal.sensitivity.value} restricted]"
    return signal.cause


def _collect_contributions(node: Node, store: GraphStore) -> list[dict]:
    """Gather upstream signals with their edge weight and (redacted) cause.

    Decomposition children are reached via edges (node -> child); dependency
    sources via edges (source -> node). The weight is looked up on the matching
    edge in each direction.
    """
    contributions: list[dict] = []

    for cid in store.children(node.id):
        child = store.get_node(cid)
        sig = child.outbound_signal
        if not sig:
            continue
        edge = next((e for e in store._edges if e.src == node.id and e.dst == cid), None)
        contributions.append({
            "title": child.title, "score": sig.score, "severity": sig.severity.value,
            "weight": edge.weight.value if edge else "medium", "cause": redact_cause(sig),
        })

    for did in store.dependencies(node.id):
        dep = store.get_node(did)
        sig = dep.outbound_signal
        if not sig:
            continue
        edge = next((e for e in store._edges if e.src == did and e.dst == node.id), None)
        contributions.append({
            "title": dep.title, "score": sig.score, "severity": sig.severity.value,
            "weight": edge.weight.value if edge else "medium", "cause": redact_cause(sig),
        })

    return contributions


def assess(node: Node, store: GraphStore, llm, vector, triggered_by: dict) -> Assessment:
    child_ids = store.children(node.id)
    dep_ids = store.dependencies(node.id)
    children = [store.get_node(c).current for c in child_ids if store.get_node(c).current]
    deps = [store.get_node(d).current for d in dep_ids if store.get_node(d).current]

    rule = score_node(node, children, deps)

    if node.kind.value == "leaf":
        # Leaf: run LLM for unstructured fields, build signal from rules + LLM
        unstructured_fields = rule.inputs.get("unstructured_fields", [])
        grounding = vector.query(node.title, k=2)
        verdict = None
        if unstructured_fields:
            binding = node.data_binding
            field_texts = []
            for uf in unstructured_fields:
                raw_val = binding.raw_values.get(uf) if binding else None
                field_texts.append(f"Field: {uf}\nRaw value: {raw_val!r}")
            combined = "\n".join(field_texts)
            verdict = llm.verify_score(
                node=node, rule_score=rule.score,
                rule_inputs={"unstructured_fields": combined, **rule.inputs},
                grounding=grounding,
            )
        else:
            verdict = llm.verify_score(
                node=node, rule_score=rule.score,
                rule_inputs=rule.inputs, grounding=grounding,
            )

        signal = _build_leaf_signal(node, rule.score, rule.inputs, verdict)
        base_sens = combine_sensitivity(
            [a.sensitivity for a in (children + deps)], node)
        sensitivity = declassify(base_sens, contributors=len(children) + len(deps))

        prev = node.current.llm_verdict.final_score if node.current else 0.0
        version = (node.current.version + 1) if node.current else 1
        upstream = {i: store.get_node(i).current.version
                    for i in (child_ids + dep_ids) if store.get_node(i).current}

        a = Assessment(
            node_id=node.id, version=version, computed_at=_now(),
            rule_score=rule.score, rule_inputs=rule.inputs, rule_version=RULE_VERSION,
            llm_verdict=verdict, grounding=grounding, upstream_versions=upstream,
            triggered_by=triggered_by, delta=abs(verdict.final_score - prev),
            sensitivity=sensitivity, change=node.pending_change,
            signal=signal,
        )
        node.current = a
        node.history.append(version)
        node.outbound_signal = signal
        return a

    # Non-leaf: collect weighted upstream signals and ask the LLM to synthesize them.
    contributions = _collect_contributions(node, store)
    grounding = vector.query(node.title, k=2)

    if not contributions:
        # No upstream signals -- no outbound signal, no synthesis.
        signal = None
        verdict_score = rule.score
        verdict = LLMVerdict(final_score=rule.score, severity=Severity.LOW,
                             rationale="no upstream signals", model=llm.model)
    else:
        try:
            verdict = llm.synthesize(node, contributions)
        except Exception:
            verdict = LLMVerdict(
                final_score=rule.score, severity=Severity.MEDIUM,
                rationale="synthesis failed", adjusted=True, model=llm.model,
                raw_response="",
            )
        verdict_score = verdict.final_score

        base_sens = combine_sensitivity(
            [a.sensitivity for a in (children + deps)], node)
        sensitivity = declassify(base_sens, contributors=len(children) + len(deps))

        signal = _build_nonleaf_signal(node, verdict, sensitivity)

    base_sens = combine_sensitivity(
        [a.sensitivity for a in (children + deps)], node)
    sensitivity = declassify(base_sens, contributors=len(children) + len(deps))

    prev = node.current.llm_verdict.final_score if node.current else 0.0
    version = (node.current.version + 1) if node.current else 1
    upstream = {i: store.get_node(i).current.version
                for i in (child_ids + dep_ids) if store.get_node(i).current}

    a = Assessment(
        node_id=node.id, version=version, computed_at=_now(),
        rule_score=rule.score, rule_inputs=rule.inputs, rule_version=RULE_VERSION,
        llm_verdict=verdict, grounding=grounding, upstream_versions=upstream,
        triggered_by=triggered_by,
        delta=abs(verdict_score - prev) if signal else 0.0,
        sensitivity=sensitivity, change=node.pending_change,
        signal=signal,
    )
    node.current = a
    node.history.append(version)
    node.outbound_signal = signal
    # Cache inbound signals for UI display
    node.inbound_signals = []
    for cid in child_ids:
        child = store.get_node(cid)
        if child.outbound_signal:
            node.inbound_signals.append(child.outbound_signal)
    for did in dep_ids:
        dep = store.get_node(did)
        if dep.outbound_signal:
            node.inbound_signals.append(dep.outbound_signal)

    return a
