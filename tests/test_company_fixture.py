from foursight.company_fixture import parse_company
from foursight.models import EdgeType

def test_fixture_has_required_structures():
    spec = parse_company()
    parents, children, has_dep = {}, {}, False
    for src, dst, etype, weight in spec.edges:
        if etype == EdgeType.DECOMPOSITION:
            parents[dst] = parents.get(dst, 0) + 1
            children[src] = children.get(src, 0) + 1
        else:
            has_dep = True
    assert any(c >= 2 for c in parents.values())        # a diamond (shared dependency)
    assert has_dep                                       # a sideways dependency edge
    assert any(c >= 3 for c in children.values())        # an aggregate that declassifies
    assert any(n.data_binding and n.data_binding.sensitivity.value == "confidential"
               for n in spec.nodes)                      # a sensitive node
    assert spec.policy_docs                              # grounding docs present
