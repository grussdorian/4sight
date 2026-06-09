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


def test_threshold_rules_translated_to_field_rules():
    """Old-format threshold_rules in the topology must be translated to
    field_rules (not silently dropped), while the generic effect_score
    ladder is retained so the simulate-change demo still fires."""
    spec = parse_supply_chain()
    sumco = next(n for n in spec.nodes if n.id == "sumco_yield")
    fields = {fr.field for fr in sumco.data_binding.field_rules}
    assert "yield_pct" in fields, "threshold_rules field was dropped on load"
    assert "effect_score" in fields, "generic ladder lost; simulate-change would break"


def test_leaf_has_real_field_and_description():
    spec = parse_supply_chain()
    sumco = next(n for n in spec.nodes if n.id == "sumco_yield")
    assert sumco.description, "description not loaded from topology"
    fields = {fr.field for fr in sumco.data_binding.field_rules}
    assert "yield_pct" in fields and "effect_score" in fields
    assert "leaf_metrics" in sumco.data_binding.query
