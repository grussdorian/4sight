from __future__ import annotations
from datetime import datetime, timezone
from .models import Report, DriverBullet, Viewer
from .graph_store import GraphStore
from .sensitivity import project_report


def _now(): return datetime.now(timezone.utc)


def generate_report(node, store: GraphStore, llm) -> Report:
    child_ids = store.children(node.id)
    dep_ids = store.dependencies(node.id)
    contribs = [store.get_node(i) for i in (child_ids + dep_ids) if store.get_node(i).current]
    contribs.sort(key=lambda n: n.current.llm_verdict.final_score, reverse=True)

    if contribs:
        drivers = [DriverBullet(node_id=n.id, severity=n.current.llm_verdict.severity,
                                line=f"{n.title}: {n.current.llm_verdict.severity.value}")
                   for n in contribs[:3]]
    elif node.kind.value == "leaf" and node.current.change:
        drivers = [DriverBullet(node_id=node.id, severity=node.current.llm_verdict.severity,
                                line=f"{node.current.change.source}: effect {node.current.llm_verdict.final_score:.0f}")]
    else:
        drivers = [DriverBullet(node_id=node.id, severity=node.current.llm_verdict.severity,
                                line=f"{node.title}: score {node.current.llm_verdict.final_score:.0f}")]

    overall = llm.generate_overall(node, drivers)
    report = Report(node_id=node.id, version=node.current.version, generated_at=_now(),
                    severity=node.current.llm_verdict.severity, overall=overall,
                    drivers=drivers, changed_since=[n.title for n in contribs if len(n.history) > 1][:3],
                    watch_items=[], grounding=node.current.grounding, disclosure=node.current.sensitivity)
    node.report = report
    return report


def static_report(node, store: GraphStore) -> Report:
    """Build a report WITHOUT calling the LLM, using the assessment's own
    rationale as the overall text. Used after a single batch assessment so the
    whole graph costs exactly one LLM call (no per-node report generation)."""
    child_ids = store.children(node.id)
    dep_ids = store.dependencies(node.id)
    contribs = [store.get_node(i) for i in (child_ids + dep_ids) if store.get_node(i).current]
    contribs.sort(key=lambda n: n.current.llm_verdict.final_score, reverse=True)

    if contribs:
        drivers = [DriverBullet(node_id=n.id, severity=n.current.llm_verdict.severity,
                                line=f"{n.title}: {n.current.llm_verdict.severity.value}")
                   for n in contribs[:3]]
    elif node.kind.value == "leaf" and node.current.change:
        drivers = [DriverBullet(node_id=node.id, severity=node.current.llm_verdict.severity,
                                line=f"{node.current.change.source}: effect {node.current.llm_verdict.final_score:.0f}")]
    else:
        drivers = [DriverBullet(node_id=node.id, severity=node.current.llm_verdict.severity,
                                line=f"{node.title}: score {node.current.llm_verdict.final_score:.0f}")]

    overall = node.current.llm_verdict.rationale or f"{node.title} risk assessed."
    report = Report(node_id=node.id, version=node.current.version, generated_at=_now(),
                    severity=node.current.llm_verdict.severity, overall=overall,
                    drivers=drivers, changed_since=[n.title for n in contribs if len(n.history) > 1][:3],
                    watch_items=[], grounding=node.current.grounding, disclosure=node.current.sensitivity)
    node.report = report
    return report


def get_report(node_id: str, store: GraphStore, viewer: Viewer) -> Report | None:
    node = store.get_node(node_id)
    return project_report(node.report, viewer) if node.report else None


def trace_to_source(node_id: str, store: GraphStore) -> dict:
    hops, cur, seen = [], node_id, set()
    while cur and cur not in seen:
        seen.add(cur)
        hops.append(cur)
        node = store.get_node(cur)
        if node.current and node.current.change:
            return {"path": hops, "origin": node.current.change}
        preds = [p for p in store.influence_predecessors(cur) if store.get_node(p).current]
        if not preds:
            break
        cur = max(preds, key=lambda p: store.get_node(p).current.llm_verdict.final_score)
    return {"path": hops, "origin": None}
