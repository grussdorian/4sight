from __future__ import annotations
from datetime import datetime, timezone
from typing import Callable, Optional
from fastmcp import FastMCP
from .models import ChangeEvent, Role, Sensitivity, Viewer


def _now() -> datetime:
    return datetime.now(timezone.utc)


def build_mcp(store, engine, get_report: Callable, trace: Callable, name: str = "4sight",
              flatten=None, llm=None) -> FastMCP:
    mcp = FastMCP(name)

    @mcp.tool
    def list_nodes() -> list[dict]:
        """List every node in the graph with its current risk summary.

        Returns one entry per node: {id, title, kind, severity, score}.
        `severity` and `score` are null for nodes that have not been
        assessed yet (no current assessment).
        """
        out: list[dict] = []
        for nid in store.all_ids():
            node = store.get_node(nid)
            current = node.current
            out.append({
                "id": node.id,
                "title": node.title,
                "kind": node.kind.value,
                "severity": current.llm_verdict.severity.value if current else None,
                "score": current.llm_verdict.final_score if current else None,
            })
        return out

    @mcp.tool
    def get_node_report(node_id: str, role: str = "reviewer") -> Optional[dict]:
        """Get the risk report for a node, projected for a viewer role.

        `role` is "reviewer" (sees only internal-or-lower detail; confidential
        sources are redacted) or "privileged" (sees everything). Returns the
        report as a JSON object, or null if the node has no report or does not
        exist.
        """
        try:
            viewer = Viewer(id="agent", role=Role(role))
            report = get_report(node_id, store, viewer)
        except (KeyError, ValueError):
            return None
        return report.model_dump(mode="json") if report is not None else None

    @mcp.tool
    def trace_risk(node_id: str) -> dict:
        """Trace a node's risk back to the upstream change that drove it.

        Returns {path, origin}: `path` is the chain of node ids from the given
        node toward the source, and `origin` is the originating change event
        (JSON object) or null if no originating change was found.
        """
        try:
            result = trace(node_id, store)
        except KeyError:
            return {"path": [node_id], "origin": None}
        origin = result.get("origin")
        return {
            "path": result.get("path", []),
            "origin": origin.model_dump(mode="json") if origin is not None else None,
        }

    @mcp.tool
    def get_neighbors(node_id: str) -> dict:
        """Get a node's direct graph neighbors.

        Returns {children, parents, dependencies, dependents}, each a list of
        node ids. Unknown nodes yield empty lists.
        """
        try:
            return {
                "children": store.children(node_id),
                "parents": store.parents(node_id),
                "dependencies": store.dependencies(node_id),
                "dependents": store.dependents(node_id),
            }
        except KeyError:
            return {"children": [], "parents": [], "dependencies": [], "dependents": []}

    @mcp.tool
    def simulate_change(node_id: str, effect_score: float,
                        source: str = "Leave Calendar") -> dict:
        """Simulate a data-source change on a node and propagate it.

        Injects a change with the given `effect_score` from `source`, then
        re-scores the affected part of the graph. Returns {changed}: the list
        of node ids whose risk changed. Returns an empty list for unknown nodes.
        """
        try:
            change = ChangeEvent(source=source, record_ref=node_id,
                                 after={"effect_score": effect_score}, at=_now(),
                                 sensitivity=Sensitivity.INTERNAL)
            engine.on_data_change(node_id, change)
            changed = engine.fire_node(node_id)
        except KeyError:
            return {"changed": []}
        return {"changed": changed}

    @mcp.tool
    def batch_assess(mode: str = "full") -> list[dict]:
        """Assess every node in the graph in a single LLM call.

        Flattens the graph in topological order, sends all nodes to the LLM
        as a batch, and returns an assessment for every node. mode='full'
        sends the entire graph. mode='delta' sends only nodes with non-zero
        delta_accumulator.
        """
        if flatten is None or llm is None:
            return []
        from .flatten import assessment_from_batch
        system, messages = flatten.build_batch_prompt(mode=mode)
        raw = llm.batch_assess(system, messages[0]["content"])
        assessments = flatten.parse_batch_response(raw)
        for entry in assessments:
            node = store.get_node(entry["node_id"])
            node.current = assessment_from_batch(node, entry)
            node.history.append(node.current.version)
            node.outbound_signal = node.current.signal
            node.delta_accumulator = 0.0
        return assessments

    return mcp


def build_fake() -> FastMCP:
    from .fakes import fake_seed, fake_get_report, fake_trace
    store, engine, _ = fake_seed()
    return build_mcp(store, engine, fake_get_report, fake_trace)


def build_real() -> FastMCP:
    from .seed import load_company
    from .reports import get_report, trace_to_source
    store, engine, _ = load_company()
    return build_mcp(store, engine, get_report, trace_to_source)


mcp = build_real()


if __name__ == "__main__":
    mcp.run()
