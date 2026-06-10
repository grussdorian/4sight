"""A Telegram push fires when the program ROOT node's severity escalates to a
higher band than the previous assessment -- and only then (not on the boot
baseline, not on an unchanged or decreasing severity)."""
import json
import re

from fastapi.testclient import TestClient

from foursight.api import build_app
from foursight.seed import load_supply_chain
from foursight.notify import severity_increased


class SpyLLM:
    """Batch LLM that scores every node a configurable severity."""
    def __init__(self, severity="low"):
        self.model = "spy"
        self.severity = severity

    def batch_assess(self, system, prompt):
        ids = []
        for m in re.findall(r"id=([A-Za-z0-9_]+)\)", prompt):
            if m not in ids:
                ids.append(m)
        return json.dumps([{"node_id": i, "final_score": 80.0,
                            "severity": self.severity, "rationale": "spy"} for i in ids])

    def summarize(self, node, chunks):
        return "spy"

    def synthesize(self, *a, **k):
        raise AssertionError("per-node synthesize called")

    def verify_score(self, *a, **k):
        raise AssertionError("per-node verify_score called")


class FakeNotifier:
    def __init__(self):
        self.sent = []

    @property
    def enabled(self):
        return True

    def send(self, text):
        self.sent.append(text)


def _bump_dirty(c):
    """Force a re-assessment by editing a leaf reading (marks its cone dirty)."""
    c.post("/node/sumco_yield/readings", json={"readings": {"yield_pct": 40}})


def test_severity_increased_helper():
    assert severity_increased("low", "high") is True
    assert severity_increased("high", "critical") is True
    assert severity_increased("critical", "low") is False
    assert severity_increased("medium", "medium") is False
    assert severity_increased(None, "critical") is False  # baseline never alerts


def test_push_on_root_escalation():
    notif = FakeNotifier()
    spy = SpyLLM(severity="low")
    c = TestClient(build_app(seed_fn=load_supply_chain, llm=spy, notifier=notif))
    c.post("/assess")                       # baseline: root low, prev None -> no push
    assert notif.sent == []

    spy.severity = "critical"
    _bump_dirty(c)
    c.post("/assess")                       # root low -> critical -> push
    assert len(notif.sent) == 1
    assert "CRITICAL" in notif.sent[0]
    assert "fab17" in notif.sent[0].lower() or "Fab 17" in notif.sent[0]


def test_no_push_when_root_decreases_or_unchanged():
    notif = FakeNotifier()
    spy = SpyLLM(severity="critical")
    c = TestClient(build_app(seed_fn=load_supply_chain, llm=spy, notifier=notif))
    c.post("/assess")                       # baseline critical, prev None -> no push
    assert notif.sent == []

    spy.severity = "low"
    _bump_dirty(c)
    c.post("/assess")                       # critical -> low: a decrease -> no push
    assert notif.sent == []


def test_disabled_notifier_is_silent():
    # No token/chat_id -> notifier disabled -> assessment still succeeds.
    from foursight.notify import TelegramNotifier
    n = TelegramNotifier(token="", chat_id="")
    assert n.enabled is False
    n.send("should be a no-op")   # must not raise
