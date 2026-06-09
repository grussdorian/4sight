from __future__ import annotations
import random
from .models import Node, NodeKind, EdgeType, DataBinding, FieldRule, Severity
from .graph_store import GraphStore


def random_dag(n_tasks: int = 12, n_leaves: int = 6, seed: int = 0) -> tuple[GraphStore, list[str]]:
    rng = random.Random(seed)
    s = GraphStore()
    tasks = [f"t{i}" for i in range(n_tasks)]
    leaves = [f"l{i}" for i in range(n_leaves)]
    for t in tasks:
        s.add_node(Node(id=t, kind=NodeKind.TASK, title=t))
    for leaf in leaves:
        s.add_node(Node(id=leaf, kind=NodeKind.LEAF, title=leaf,
                        data_binding=DataBinding(adapter_id=leaf, field_rules=[
                            FieldRule(field="effect_score", kind="structured", operator=">=",
                                      expected=75.0, severity_on_breach=Severity.CRITICAL),
                            FieldRule(field="effect_score", kind="structured", operator=">=",
                                      expected=50.0, severity_on_breach=Severity.HIGH),
                            FieldRule(field="effect_score", kind="structured", operator=">=",
                                      expected=25.0, severity_on_breach=Severity.MEDIUM),
                        ]), raw={}))
    for i in range(1, n_tasks):                      # each later task gets an earlier parent
        try: s.add_edge(tasks[rng.randrange(i)], tasks[i], EdgeType.DECOMPOSITION)
        except ValueError: pass
    for leaf in leaves:                              # attach each leaf under a random task
        try: s.add_edge(tasks[rng.randrange(n_tasks)], leaf, EdgeType.DECOMPOSITION)
        except ValueError: pass
    for _ in range(n_tasks // 3):                    # a few sideways edges (skip if they cycle)
        a, b = rng.choice(tasks + leaves), rng.choice(tasks)
        if a != b:
            try: s.add_edge(a, b, EdgeType.DEPENDENCY)
            except ValueError: pass
    return s, leaves
