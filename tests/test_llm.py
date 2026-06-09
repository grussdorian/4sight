from foursight.models import Node, NodeKind, Severity
from foursight.llm import FakeLLM


def test_fake_bumps_single_owner():
    v = FakeLLM().verify_score(node=Node(id="l", kind=NodeKind.LEAF, title="payroll"),
                               rule_score=40.0, rule_inputs={"single_owner": True}, grounding=[])
    assert v.final_score >= 85.0 and v.adjusted is True
    assert v.raw_response == ""


def test_fake_passthrough():
    v = FakeLLM().verify_score(node=Node(id="t", kind=NodeKind.TASK, title="t"),
                               rule_score=20.0, rule_inputs={}, grounding=[])
    assert v.final_score == 20.0 and v.adjusted is False
    assert v.raw_response == ""


def test_fake_summarize_mentions_title_and_count():
    from foursight.llm import FakeLLM
    from foursight.models import Node, NodeKind
    s = FakeLLM().summarize(Node(id="n", kind=NodeKind.TASK, title="Lithography"),
                            ["chunk a", "chunk b"])
    assert "Lithography" in s
    assert "2" in s


def test_fake_summarize_no_chunks():
    from foursight.llm import FakeLLM
    from foursight.models import Node, NodeKind
    s = FakeLLM().summarize(Node(id="n", kind=NodeKind.TASK, title="X"), [])
    assert isinstance(s, str) and s
