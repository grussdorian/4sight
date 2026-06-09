from __future__ import annotations
from .models import Node, NodeKind, EdgeType, DataBinding, Sensitivity
from .graph_store import GraphStore
from .propagation import Engine
from .reports import generate_report


def _task(s, nid, title):
    s.add_node(Node(id=nid, kind=NodeKind.TASK, title=title))


def _leaf(s, nid, title, sens=Sensitivity.INTERNAL):
    from .models import FieldRule, Severity as Sev
    s.add_node(Node(id=nid, kind=NodeKind.LEAF, title=title,
                    data_binding=DataBinding(
                        adapter_id=nid, sensitivity=sens,
                        field_rules=[
                            FieldRule(field="effect_score", kind="structured", operator=">=",
                                      expected=75.0, severity_on_breach=Sev.CRITICAL),
                            FieldRule(field="effect_score", kind="structured", operator=">=",
                                      expected=50.0, severity_on_breach=Sev.HIGH),
                            FieldRule(field="effect_score", kind="structured", operator=">=",
                                      expected=25.0, severity_on_breach=Sev.MEDIUM),
                            FieldRule(field="capacity_drop_pct", kind="structured", operator=">=",
                                      expected=50.0, severity_on_breach=Sev.HIGH),
                            FieldRule(field="single_owner", kind="structured", operator="==",
                                      expected=1.0, severity_on_breach=Sev.CRITICAL),
                            FieldRule(field="data_age_h", kind="structured", operator=">",
                                      expected=120.0, severity_on_breach=Sev.MEDIUM),
                        ],
                    ), raw={}))


def build_seed(llm=None, vector=None, conn=None, assess=True):
    from .llm import FakeLLM
    from .vector_store import FakeVector
    s = GraphStore()
    for nid, title in [("root", "Q3 Launch Readiness"), ("customer_portal", "Customer Portal"),
                       ("payments", "Payments Platform"), ("platform_team", "Platform Team"),
                       ("payments_team", "Payments Team"), ("personnel_budget", "Personnel Budget")]:
        _task(s, nid, title)
    _leaf(s, "alice_owner", "Alice (payroll ownership)")
    _leaf(s, "skills_matrix", "Skills Matrix")
    _leaf(s, "comp_pool", "Eng compensation pool", Sensitivity.CONFIDENTIAL)
    for a, b in [("root", "customer_portal"), ("root", "payments"), ("root", "personnel_budget"),
                 ("customer_portal", "platform_team"), ("payments", "platform_team"),
                 ("payments", "payments_team"), ("platform_team", "alice_owner"),
                 ("platform_team", "skills_matrix"), ("personnel_budget", "comp_pool")]:
        s.add_edge(a, b, EdgeType.DECOMPOSITION)
    s.add_edge("alice_owner", "payments_team", EdgeType.DEPENDENCY)   # cross-branch sideways
    eng = Engine(s, llm or FakeLLM(), vector or FakeVector(), generate_report)
    if assess:
        eng.run_full()
    return s, eng, {}


def load_company(path=None, llm=None, vector=None, conn=None, assess=True):
    from .company_fixture import parse_company, FIXTURES
    from .vector_store import FakeVector
    from .llm import FakeLLM
    spec = parse_company(path or FIXTURES)
    s = GraphStore()
    for node in spec.nodes:
        s.add_node(node)
    for src, dst, etype, weight in spec.edges:
        s.add_edge(src, dst, etype, weight)
    vector = vector or FakeVector()
    for doc_id, text in spec.policy_docs:
        vector.add(doc_id, text)
    eng = Engine(s, llm or FakeLLM(), vector, generate_report)
    if assess:
        eng.run_full()
    return s, eng, {}


def load_supply_chain(path=None, llm=None, vector=None, conn=None, assess=True):
    from .supply_chain_fixture import parse_supply_chain, metric_baselines, FIXTURES
    from .vector_store import FakeVector
    from .llm import FakeLLM
    spec = parse_supply_chain(path or FIXTURES)
    s = GraphStore()
    for node in spec.nodes:
        s.add_node(node)
    for src, dst, etype, weight in spec.edges:
        s.add_edge(src, dst, etype, weight)
    vector = vector or FakeVector()
    for doc_id, text in spec.policy_docs:
        vector.add(doc_id, text)
    if conn is not None:
        # Seed the operational data tables and load each leaf's raw values from
        # SQL so the initial assessment reflects real (healthy) polled data.
        from .db import seed_metrics, read_metrics
        seed_metrics(conn, metric_baselines())
        for nid in s.all_ids():
            node = s.get_node(nid)
            if node.data_binding:
                node.data_binding.raw_values.update(read_metrics(conn, nid))
    eng = Engine(s, llm or FakeLLM(), vector, generate_report)
    if assess:
        eng.run_full()
    return s, eng, {}
