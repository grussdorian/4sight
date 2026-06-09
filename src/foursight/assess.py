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

    sensitivity_val = binding.sensitivity if binding else Severity.MEDIUM
    min_disc = binding.min_disclosure if binding else Severity.MEDIUM

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


def _build_synthesis_prompt(node: Node, store: GraphStore,
                             redact_for_sensitivity) -> str | None:
    """Build the LLM prompt for synthesizing upstream signals into a risk verdict."""
    child_ids = store.children(node.id)
    dep_ids = store.dependencies(node.id)

    signal_lines = []
    for cid in child_ids:
        child = store.get_node(cid)
        if not child.outbound_signal:
            continue
        sig = child.outbound_signal
        edge = next((e for e in store._edges
                     if e.src == cid and e.dst == node.id), None)
        weight = edge.weight.value if edge else "medium"
        cause_text = sig.cause
        if sig.sensitivity != Sensitivity("public"):
            cause_text = redact_for_sensitivity(sig)
        signal_lines.append(
            f"- {child.title} [weight: {weight}, score: {sig.score:.0f}, "
            f"severity: {sig.severity.value}]\n  cause: {cause_text}"
        )

    for did in dep_ids:
        dep = store.get_node(did)
        if not dep.outbound_signal:
            continue
        sig = dep.outbound_signal
        edge = next((e for e in store._edges
                     if e.src == did and e.dst == node.id), None)
        weight = edge.weight.value if edge else "medium"
        cause_text = sig.cause
        if sig.sensitivity != Sensitivity("public"):
            cause_text = redact_for_sensitivity(sig)
        signal_lines.append(
            f"- {dep.title} [weight: {weight}, score: {sig.score:.0f}, "
            f"severity: {sig.severity.value}]\n  cause: {cause_text}"
        )

    if not signal_lines:
        return None  # No upstream signals — skip LLM call

    prompt = (
        f"Task: {node.title}\n"
        f"Description: {node.description or 'No description'}\n\n"
        f"Upstream signals:\n"
        + "\n".join(signal_lines)
        + "\n\nSynthesize these into a single risk assessment. "
          "Weight tags tell you how seriously to treat each signal. "
          "If a cause is redacted, rely on the score and severity alone. "
          "Consider compounding effects — multiple moderate signals may "
          "compound into a higher risk than any individually.\n\n"
          'Reply with JSON only: {"score": <number 0-100>, '
          '"severity": "low"|"medium"|"high"|"critical", '
          '"cause": "<2-3 sentence synthesis>"}'
    )
    return prompt


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

    # Non-leaf: collect signals, build synthesis prompt, ask LLM to synthesize
    def redact(sig: Signal) -> str:
        return f"[REDACTED - {sig.sensitivity.value} restricted]"

    prompt = _build_synthesis_prompt(node, store, redact)

    if prompt is None:
        # No upstream signals — no assessment
        signal = None
        verdict_score = rule.score
        verdict = None
    else:
        # Call LLM to synthesize
        import json as _json
        grounding = vector.query(node.title, k=2)
        try:
            raw_resp = llm.batch_assess(
                system=(
                    "You are a risk synthesis engine. You receive upstream signals "
                    "from a task's dependencies. Each signal arrives through an edge "
                    "with a weight tag indicating how important that connection is. "
                    "Synthesize them into one risk assessment."
                ),
                prompt=prompt,
            )
            data = _json.loads(raw_resp)
        except Exception:
            data = {"score": rule.score, "severity": "medium", "cause": "synthesis failed"}

        verdict_score = float(data.get("score", rule.score))
        sev_str = data.get("severity", "medium")
        sev = Severity(sev_str) if sev_str in [s.value for s in Severity] else Severity.MEDIUM

        verdict = LLMVerdict(
            final_score=verdict_score, severity=sev,
            rationale=data.get("cause", ""),
            adjusted=True, model=llm.model,
            raw_response=str(data),
        )

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
