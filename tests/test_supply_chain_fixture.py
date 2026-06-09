from foursight.supply_chain_fixture import parse_supply_chain
from foursight.models import EdgeType


def test_fixture_has_required_structures():
    spec = parse_supply_chain()
    incoming, outgoing, has_conf = {}, {}, False
    for src, dst, etype, weight in spec.edges:
        incoming[dst] = incoming.get(dst, 0) + 1
        outgoing[src] = outgoing.get(src, 0) + 1
    for n in spec.nodes:
        if n.data_binding and n.data_binding.sensitivity.value == "confidential":
            has_conf = True
    assert any(c >= 2 for c in incoming.values())  # shared dependency (multiple sources)
    assert any(c >= 3 for c in incoming.values())  # aggregate receiving from 3+ sources
    assert len(spec.edges) >= 23
    assert has_conf
    assert spec.policy_docs
    assert len(spec.nodes) >= 19
    assert len(spec.edges) >= 23
