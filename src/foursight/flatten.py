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
    score = float(entry.get("final_score", 0.0))
    sev_str = entry.get("severity", "")
    severity = Severity(sev_str) if sev_str in [s.value for s in Severity] else severity_from_score(score)
    rationale = entry.get("rationale") or entry.get("summary", "")

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


class FlattenEngine:
    def __init__(self, store: GraphStore, vector=None) -> None:
        self.store = store
        self.vector = vector

    def _edge_weight(self, src: str, dst: str) -> str:
        e = next((e for e in self.store._edges if e.src == src and e.dst == dst), None)
        return e.weight.value if e else "medium"

    def _title(self, nid: str) -> str:
        try:
            return self.store.get_node(nid).title
        except Exception:
            return nid

    def _render_node(self, node: Node) -> str:
        lines = [
            f"Node: {node.title} (kind={node.kind.value}, id={node.id})",
            f"Description: {node.description or 'none'}",
        ]
        # Structure: what feeds this node, with edge importance.
        children = self.store.children(node.id)
        if children:
            lines.append("Decomposition children: " + ", ".join(
                f"{self._title(c)} (id={c}, weight={self._edge_weight(node.id, c)})"
                for c in children))
        deps = self.store.dependencies(node.id)
        if deps:
            lines.append("Dependencies: " + ", ".join(
                f"{self._title(d)} (id={d}, weight={self._edge_weight(d, node.id)})"
                for d in deps))

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

        if node.current:
            cur = node.current
            lines.append(
                f"Previous assessment: {cur.llm_verdict.final_score:.0f} "
                f"({cur.llm_verdict.severity.value})"
            )
        return "\n".join(lines)

    def flatten_full(self) -> str:
        order = self.store.topo_order(set(self.store.all_ids()))
        blocks = []
        for nid in order:
            blocks.append(self._render_node(self.store.get_node(nid)))
        return "\n---\n".join(blocks)

    def flatten_delta(self) -> str:
        order = self.store.topo_order(set(self.store.all_ids()))
        blocks = []
        for nid in order:
            node = self.store.get_node(nid)
            if node.delta_accumulator > 0:
                blocks.append(self._render_node(node))
        return "\n---\n".join(blocks) if blocks else ""

    def build_batch_prompt(self, mode: str = "full") -> tuple[str, list[dict]]:
        graph_text = self.flatten_full() if mode == "full" else self.flatten_delta()
        system = (
            "You are an operational risk assessor for a semiconductor fab supply "
            "chain. You receive the ENTIRE graph flattened in topological order "
            "(leaves first, root last): each node lists its description, the "
            "children/dependencies that feed it with an edge weight "
            "(critical/high/medium/low importance), any live data-source readings "
            "and rule findings, and grounding context from policy documents.\n\n"
            "Assess every node in one pass:\n"
            "- Leaf data sources: score from their rule findings and readings.\n"
            "- Tasks: synthesize the signals from their children and dependencies. "
            "Weight each upstream signal by its edge importance (critical dominates, "
            "low is weak). Multiple moderate signals can compound into higher risk.\n"
            "- Risk flows leaf -> task -> root; use the grounding context for "
            "domain judgment (e.g. single-source suppliers, single-owner roles).\n\n"
            "For every node return an object: node_id, final_score (0-100), severity "
            "(low/medium/high/critical), rationale (ONE short clause, <= 15 words). "
            "Be terse to keep the response small. "
            "Reply with ONLY a compact JSON array of these objects, one per node, "
            "no markdown, no prose outside the array."
        )
        prompt = (
            "Assess every node in this graph. Return a JSON array with one "
            f"object per node.\n\n{graph_text}"
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
            start, end = text.find("["), text.rfind("]")
            if start != -1 and end > start:
                return json.loads(text[start:end + 1])
            raise
