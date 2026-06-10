from __future__ import annotations
import os
from typing import Protocol
from .models import Node, LLMVerdict, Grounding, DriverBullet, Severity, severity_from_score


class LLM(Protocol):
    model: str

    def verify_score(self, node: Node, rule_score: float, rule_inputs: dict,
                     grounding: list[Grounding]) -> LLMVerdict: ...

    def generate_overall(self, node: Node, drivers: list[DriverBullet]) -> str: ...

    def synthesize(self, node: Node, contributions: list[dict]) -> LLMVerdict: ...

    def summarize(self, node: Node, chunks: list[str]) -> str: ...

    def batch_assess(self, system: str, prompt: str) -> str: ...


# Edge weight -> how heavily a non-leaf node weighs each upstream signal.
# MEDIUM is neutral (1.0) so all-MEDIUM graphs behave exactly like max();
# CRITICAL/HIGH amplify a dominant signal, LOW discounts a weak coupling.
WEIGHT_FACTOR = {"critical": 1.25, "high": 1.1, "medium": 1.0, "low": 0.5}


class FakeLLM:
    model = "fake"

    def verify_score(self, node, rule_score, rule_inputs, grounding) -> LLMVerdict:
        score, adjusted = rule_score, False
        rationale = f"Rule score {rule_score:.0f} for {node.title}."

        # If there are unstructured (qualitative) fields, treat them as active
        # concerns only when there is actual data (effect_score from inject) or
        # a positive rule_score. A quiet qualitative leaf scores 0.
        unstructured = rule_inputs.get("unstructured_fields", [])
        if unstructured:
            eff = float(node.data_binding.raw_values.get("effect_score", 0)) if node.data_binding else 0.0
            if eff > 0:
                score = max(score, eff)
                adjusted = True
                rationale = f"Qualitative concern: {', '.join(unstructured)}. " + rationale
            elif rule_score > 0:
                score = rule_score
                adjusted = True
                rationale = f"Unstructured fields ({len(unstructured)}) present. " + rationale
            # else: no active data — score stays at 0 (the leaf is quiet)

        if rule_inputs.get("single_owner"):
            score = max(score, 85.0)
            adjusted = True
            rationale = "Single-owner dependency raises severity. " + rationale
        return LLMVerdict(final_score=score, severity=severity_from_score(score),
                          rationale=rationale, adjusted=adjusted, model=self.model)

    def synthesize(self, node, contributions) -> LLMVerdict:
        """Weight-aware synthesis of upstream signals into one verdict.

        Each contribution is {title, score, severity, weight, cause}. The edge
        weight scales each signal; the dominant (highest weighted) score drives
        the result, so edge importance actually changes the outcome.
        """
        if not contributions:
            return LLMVerdict(final_score=0.0, severity=Severity.LOW,
                              rationale="No upstream signals.", model=self.model)

        def effective(c):
            return min(100.0, c["score"] * WEIGHT_FACTOR.get(c.get("weight", "medium"), 1.0))

        dominant = max(contributions, key=effective)
        final = effective(dominant)
        rationale = (
            f"Synthesized {len(contributions)} upstream signal(s); dominant driver "
            f"{dominant['title']} (weight {dominant.get('weight', 'medium')}, "
            f"score {dominant['score']:.0f})."
        )
        return LLMVerdict(final_score=final, severity=severity_from_score(final),
                          rationale=rationale, adjusted=True, model=self.model)

    def generate_overall(self, node, drivers) -> str:
        sev = node.current.llm_verdict.severity.value if node.current else "unknown"
        if not drivers:
            return f"{node.title} is at {sev} risk."
        first = drivers[0]
        if first.node_id == node.id:
            return f"{node.title} is at {sev} risk. {first.line}."
        return f"{node.title} is at {sev} risk. Primary driver: {first.line}."

    def summarize(self, node, chunks) -> str:
        n = len(chunks)
        if not chunks:
            return f"No grounding context found for {node.title}."
        return f"Context summary for {node.title}: {n} reference(s) reviewed."

    def batch_assess(self, system: str, prompt: str) -> str:
        import json, re
        ids = re.findall(r'id=(\S+)\)', prompt)
        results = []
        for nid in ids:
            results.append({"node_id": nid, "final_score": 20.0, "severity": "low",
                           "rationale": "batch fake", "summary": f"Fake summary for {nid}"})
        return json.dumps(results) if results else json.dumps([{"node_id": "root", "final_score": 20.0, "severity": "low", "rationale": "batch fake", "summary": "Fake"}])


class DeepSeekLLM:
    model = "deepseek-v4-flash"

    def __init__(self) -> None:
        from anthropic import Anthropic
        # Model is overridable via env (deepseek-v4-flash | deepseek-v4-pro | ...).
        self.model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
        key = os.environ["DEEPSEEK_API_KEY"]
        # Anthropic endpoint for per-node verify/synthesize/summarize (thinking).
        self._client = Anthropic(api_key=key,
                                 base_url="https://api.deepseek.com/anthropic")
        # OpenAI endpoint for the single whole-graph batch call: reliable
        # structured output without the thinking endpoint's empty-response quirk.
        from openai import OpenAI
        self._oai = OpenAI(api_key=key, base_url="https://api.deepseek.com")

    def _extract_text(self, response) -> str:
        text = ""
        for block in response.content:
            if block.type == "text":
                text += block.text
        return text

    def verify_score(self, node, rule_score, rule_inputs, grounding) -> LLMVerdict:
        import json
        ctx = "\n".join(f"- {g.doc}" for g in grounding) or "none"
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=2048,
            thinking={"type": "enabled", "budget_tokens": 4096},
            system=(
                "You are an operational risk assessor for a semiconductor supply chain. "
                "Your job is to verify or adjust a rule-based risk score (0-100) using "
                "qualitative context from policy documents and domain knowledge.\n\n"
                "Scoring framework:\n"
                "- 0-24: LOW risk. Normal operations. Minor fluctuations.\n"
                "- 25-49: MEDIUM risk. Notable disruption in one area. Monitor closely.\n"
                "- 50-74: HIGH risk. Significant disruption affecting multiple dependencies. "
                "Escalate to leadership.\n"
                "- 75-100: CRITICAL risk. Severe, cascading failure across the supply chain. "
                "Immediate action required.\n\n"
                "Adjustment rules:\n"
                "- Single-owner dependencies (no backup): raise score significantly, "
                "especially if the owner is unavailable.\n"
                "- Capacity drops exceeding 30% on a sole source: escalate to HIGH or CRITICAL.\n"
                "- Fuel or logistics volatility: factor into dependent freight lanes.\n"
                "- Yield rate degradation: assess impact on downstream fabs.\n"
                "- Redundancy (multiple suppliers or buffer stock) mitigates risk: "
                "reduce score when alternatives exist.\n"
                "- Cross-branch dependencies amplify impact: a problem in one area "
                "can cascade through dependency edges.\n\n"
                "Your rationale must reference specific risk factors from the rule inputs, "
                "explain why you adjusted or kept the score, and note any mitigating "
                "or amplifying factors. Be concise but thorough."
            ),
            messages=[{"role": "user", "content": (
                f"Task: '{node.title}'\n"
                f"Rule score: {rule_score}\n"
                f"Inputs: {json.dumps(rule_inputs)}\n"
                f"Relevant policies:\n{ctx}\n\n"
                'Reply with JSON only: {"final_score": <number 0-100>, '
                '"rationale": "<your reasoning>", "adjusted": <true|false>}'
            )}],
        )
        raw = self._extract_text(resp)
        data = json.loads(raw)
        score = float(data["final_score"])
        return LLMVerdict(final_score=score, severity=severity_from_score(score),
                          rationale=data.get("rationale", ""),
                          adjusted=bool(data.get("adjusted", False)),
                          model=self.model, raw_response=raw)

    def generate_overall(self, node, drivers) -> str:
        lines = "\n".join(f"- {d.line}" for d in drivers) or "none"
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=512,
            thinking={"type": "enabled", "budget_tokens": 2048},
            system=(
                "You are a risk report writer for a semiconductor supply chain. "
                "Your audience is operations leadership. Write concise, factual summaries "
                "grounded in the driver bullets provided. Never invent specifics, names, "
                "or metrics beyond what the drivers contain. Use precise operational "
                "language. If drivers mention personnel issues, note the impact on "
                "coverage. If drivers mention supplier or logistics issues, note the "
                "supply chain implications. Keep to 2-3 sentences."
            ),
            messages=[{"role": "user", "content": (
                f"Write a risk summary for '{node.title}'. Top drivers:\n{lines}"
            )}],
        )
        return self._extract_text(resp).strip()

    def synthesize(self, node, contributions) -> LLMVerdict:
        import json
        if not contributions:
            return LLMVerdict(final_score=0.0, severity=Severity.LOW,
                              rationale="No upstream signals.", model=self.model)
        signal_lines = "\n".join(
            f"- {c['title']} [weight: {c.get('weight', 'medium')}, score: {c['score']:.0f}, "
            f"severity: {c['severity']}]\n  cause: {c['cause']}"
            for c in contributions
        )
        prompt = (
            f"Task: {node.title}\n"
            f"Description: {node.description or 'No description'}\n\n"
            f"Upstream signals:\n{signal_lines}\n\n"
            "Synthesize these into a single risk assessment. Weight tags tell you how "
            "seriously to treat each signal. If a cause is redacted, rely on the score "
            "and severity alone. Consider compounding effects -- multiple moderate "
            "signals may compound into a higher risk than any individually.\n\n"
            'Reply with JSON only: {"score": <number 0-100>, '
            '"severity": "low"|"medium"|"high"|"critical", '
            '"cause": "<2-3 sentence synthesis>"}'
        )
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=2048,
            thinking={"type": "enabled", "budget_tokens": 4096},
            system=(
                "You are a risk synthesis engine. You receive upstream signals from a "
                "task's dependencies, each arriving through an edge with a weight tag "
                "indicating how important that connection is. Synthesize them into a "
                "single risk assessment."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        raw = self._extract_text(resp)
        data = json.loads(raw)
        score = float(data["score"])
        sev_str = data.get("severity", "")
        sev = Severity(sev_str) if sev_str in [s.value for s in Severity] else severity_from_score(score)
        return LLMVerdict(final_score=score, severity=sev,
                          rationale=data.get("cause", ""), adjusted=True,
                          model=self.model, raw_response=raw)

    def summarize(self, node, chunks) -> str:
        if not chunks:
            return f"No grounding context found for {node.title}."
        context = "\n\n".join(f"- {c}" for c in chunks)
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=512,
            thinking={"type": "enabled", "budget_tokens": 4000},
            system=(
                "You summarize operational context for a semiconductor fab risk "
                "dashboard. Given retrieved policy/context excerpts for a node, "
                "write 1-2 sentences describing the relevant context and what to "
                "watch. Use only the excerpts; never invent specifics, names, or "
                "numbers not present in them."
            ),
            messages=[{"role": "user", "content": (
                f"Node: {node.title}\n"
                f"Description: {node.description or 'none'}\n\n"
                f"Retrieved context:\n{context}\n\n"
                "Write the 1-2 sentence context summary."
            )}],
        )
        return self._extract_text(resp).strip()

    def batch_assess(self, system: str, prompt: str) -> str:
        # One call scores the whole graph via the OpenAI-compatible endpoint,
        # which returns structured output reliably (the Anthropic thinking
        # endpoint intermittently returned empty completions here). Retry a
        # couple of times defensively.
        for _ in range(3):
            resp = self._oai.chat.completions.create(
                model=self.model,
                max_tokens=4096,
                temperature=0,   # near-deterministic: same graph -> same scores
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": prompt}],
            )
            text = (resp.choices[0].message.content or "").strip()
            if text:
                return text
        return ""
