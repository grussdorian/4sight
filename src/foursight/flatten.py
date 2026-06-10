from __future__ import annotations
from datetime import datetime, timezone
from .graph_store import GraphStore
from .models import (
    Node, Assessment, LLMVerdict, Severity, Sensitivity, Signal, severity_from_score,
)

RULE_VERSION = "ops-risk-v2"


def assessment_from_batch(node: Node, entry: dict) -> Assessment:
    """Convert one batch_assess response entry into a real Assessment.

    The batch LLM returns plain dicts ({node_id, final_score, severity,
    rationale, summary}); writing those straight into node.current corrupted
    the graph (every later node.current.llm_verdict access blew up). This
    builds a proper, version-bumped Assessment with a propagating Signal.
    """
    raw_score = entry.get("final_score", entry.get("score", 0.0))
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(100.0, score))
    sev_str = str(entry.get("severity", "")).lower()
    severity = Severity(sev_str) if sev_str in [s.value for s in Severity] else severity_from_score(score)
    rationale = entry.get("rationale") or entry.get("summary") or ""

    prev = node.current
    version = (prev.version + 1) if isinstance(prev, Assessment) else 1
    verdict = LLMVerdict(final_score=score, severity=severity, rationale=rationale,
                         adjusted=True, model="batch")
    signal = Signal(source_node=node.id, score=score, severity=severity,
                    cause=rationale,
                    sensitivity=node.data_binding.sensitivity if node.data_binding else Sensitivity.INTERNAL)
    return Assessment(
        node_id=node.id, version=version, computed_at=datetime.now(timezone.utc),
        rule_score=score, rule_version=RULE_VERSION, llm_verdict=verdict,
        signal=signal,
    )


def input_snapshot(store, node_id: str) -> list:
    """A compact record of the inputs feeding a node (its predecessors' current
    signals), used both to anchor the prompt and to key the history."""
    snap = []
    for p in store.influence_predecessors(node_id):
        pn = store.get_node(p)
        if pn.outbound_signal:
            snap.append({"id": p, "title": pn.title,
                         "severity": pn.outbound_signal.severity.value,
                         "score": round(pn.outbound_signal.score)})
    return snap


class FlattenEngine:
    def __init__(self, store: GraphStore, vector=None, conn=None) -> None:
        self.store = store
        self.vector = vector
        self.conn = conn

    def _input_weight(self, pred: str, node_id: str) -> str:
        # The stored edge between an input and a node may be in either
        # orientation (dependency src->dst, or decomposition parent->child).
        e = next((e for e in self.store._edges
                  if {e.src, e.dst} == {pred, node_id}), None)
        return e.weight.value if e else "medium"

    def _title(self, nid: str) -> str:
        try:
            return self.store.get_node(nid).title
        except Exception:
            return nid

    def _input_current(self, pred) -> str:
        """The input's current signal for the prompt. For a leaf we use its FRESH
        rule-based score (so a brand-new or just-changed leaf is visible to its
        consumer even before it has an outbound_signal); otherwise the cached
        outbound signal."""
        from .models import NodeKind, severity_from_score
        if pred.kind == NodeKind.LEAF and pred.data_binding:
            from .rules import score_leaf
            rs = score_leaf(pred).score
            sig = pred.outbound_signal.score if pred.outbound_signal else 0.0
            score = max(rs, sig)
            return f", current={severity_from_score(score).value}/{score:.0f}"
        if pred.outbound_signal:
            return f", current={pred.outbound_signal.severity.value}/{pred.outbound_signal.score:.0f}"
        return ""

    def _render_node(self, node: Node) -> str:
        lines = [
            f"Node: {node.title} (kind={node.kind.value}, id={node.id})",
            f"Description: {node.description or 'none'}",
        ]
        # Structure by influence direction: inputs feed this node. Each input's
        # CURRENT signal is included so the node can be scored even when its
        # inputs are not part of this (delta) batch.
        preds = self.store.influence_predecessors(node.id)
        if preds:
            parts = []
            for p in preds:
                pn = self.store.get_node(p)
                parts.append(f"{self._title(p)} (id={p}, weight={self._input_weight(p, node.id)}"
                             f"{self._input_current(pn)})")
            lines.append("Inputs (signals feeding this node): " + ", ".join(parts))

        # Leaf data: real query, live readings, and deterministic rule findings.
        binding = node.data_binding
        if binding:
            if binding.query:
                lines.append(f"Data source: {binding.adapter_id} | query: {binding.query}")
            if binding.raw_values:
                readings = ", ".join(f"{k}={v}" for k, v in binding.raw_values.items())
                lines.append(f"Current readings: {readings}")
            from .rules import score_leaf
            findings = score_leaf(node).inputs.get("breached", [])
            if findings:
                lines.append("Rule findings: " + "; ".join(
                    f"{b['field']}={b['value']} {b['operator']} {b['expected']} -> {b['severity']}"
                    for b in findings))
            else:
                lines.append("Rule findings: within thresholds")

        # Accumulated grounding context from the vector store (Chroma).
        if self.vector is not None:
            try:
                chunks = self.vector.query_texts(f"{node.title} {node.description}", k=1)
            except Exception:
                chunks = []
            if chunks:
                snippet = chunks[0].replace("\n", " ").strip()[:240]
                lines.append(f"Context: {snippet}")

        # Past judgments anchor this node so it does not drift: identical inputs
        # should reproduce the same severity it gave before.
        if self.conn is not None:
            try:
                from .db import read_history
                hist = read_history(self.conn, node.id)
            except Exception:
                hist = []
            if hist:
                hl = []
                for h in hist[-6:]:
                    ins = ", ".join(f"{i.get('title', i.get('id'))}={i['severity']}/{i['score']}"
                                    for i in h["inputs"]) or "(none)"
                    hl.append(f"  inputs[{ins}] -> {h['severity']}")
                lines.append("Past judgments (reproduce the same severity for matching inputs):\n"
                             + "\n".join(hl))

        # Deliberately NOT including this node's own previous score directly: the
        # prompt stays a pure function of structure + data + history, so
        # temperature-0 output is stable across re-runs when nothing changed.
        return "\n".join(lines)

    def flatten_full(self) -> str:
        return self.flatten_scope(set(self.store.all_ids()))

    def flatten_scope(self, scope) -> str:
        """Flatten only the given nodes (topologically ordered)."""
        order = self.store.topo_order(set(scope))
        blocks = [self._render_node(self.store.get_node(nid)) for nid in order]
        return "\n---\n".join(blocks)

    def build_batch_prompt(self, mode: str = "full", scope=None) -> tuple[str, list[dict]]:
        graph_text = self.flatten_scope(scope) if scope is not None else self.flatten_full()
        system = (
            "You are an operational risk assessor for a semiconductor fab supply "
            "chain. You receive a set of nodes flattened in topological order "
            "(inputs first). Each node lists its description, its inputs with an "
            "edge weight (critical/high/medium/low) and each input's CURRENT "
            "signal (severity/score), any live data-source readings and rule "
            "findings, and grounding context from policy documents.\n\n"
            "Score each node you are given:\n"
            "- Leaf data sources: score from their rule findings and readings.\n"
            "- Tasks: synthesize the inputs' current signals. Weight each by its "
            "edge importance (critical dominates, low is weak). Multiple moderate "
            "signals can compound into higher risk. A single-source/single-owner "
            "input keeps risk elevated; redundancy (several inputs, only one bad) "
            "mitigates it.\n"
            "- Be consistent and deterministic: identical inputs must yield the "
            "same score. Do not invent volatility that the data does not show.\n"
            "- A node may list 'Past judgments' (prior inputs -> severity). Honor "
            "them: if the current inputs match a past case, output the SAME "
            "severity. Only deviate when the inputs genuinely differ from every "
            "past case, so a node does not drift when an upstream value returns "
            "to a level it saw before.\n\n"
            "For every node return an object: node_id, final_score (0-100), severity "
            "(low/medium/high/critical), rationale (ONE short clause, <= 15 words). "
            "Reply with ONLY a compact JSON array of these objects, one per node "
            "you were given, no markdown, no prose outside the array."
        )
        prompt = (
            "Score every node below. Return a JSON array with one object per "
            f"node.\n\n{graph_text}"
        )
        return system, [{"role": "user", "content": prompt}]

    def parse_batch_response(self, raw: str) -> list[dict]:
        """Parse the batch JSON array, tolerant of the model wrapping it in
        markdown fences or surrounding prose (DeepSeek is not always clean)."""
        import json, re
        text = (raw or "").strip()
        if not text:
            raise ValueError("empty batch response")
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        start, end = text.find("["), text.rfind("]")
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        # Salvage: collect every complete top-level {...} object, even from a
        # truncated array (model output cut off mid-response).
        objs, depth, begin = [], 0, None
        for i, ch in enumerate(text):
            if ch == "{":
                if depth == 0:
                    begin = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and begin is not None:
                    try:
                        objs.append(json.loads(text[begin:i + 1]))
                    except json.JSONDecodeError:
                        pass
                    begin = None
        if objs:
            return objs
        raise ValueError("no parseable assessments in batch response")
