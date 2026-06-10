from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from .models import ChangeEvent, Sensitivity, Viewer, Role, Node, NodeKind, EdgeType, Severity

WEB = Path(__file__).parent / "web"


def build_app(seed_fn=None, get_report_fn=None, trace_fn=None,
              db_path=None, llm=None, vector=None, context_llm=None) -> FastAPI:
    import sqlite3
    from .db import init_db, save_graph, load_graph, seed_metrics, read_metrics, set_metric
    from .triggers import TriggerEngine
    from .poll import PollService
    if seed_fn is None:
        from .seed import build_seed as seed_fn
    if get_report_fn is None:
        from .reports import get_report as get_report_fn
    if trace_fn is None:
        from .reports import trace_to_source as trace_fn

    conn = sqlite3.connect(db_path or ":memory:", check_same_thread=False)
    init_db(conn)
    persist = db_path is not None
    has_persisted = persist and conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] > 0

    if has_persisted:
        # Restore graph structure from SQLite (a prior session saved it), then
        # rebuild the ephemeral Chroma index, reseed/reload operational data,
        # and re-run a full assessment.
        from .reports import generate_report
        from .propagation import Engine
        from .vector_store import FakeVector
        from .llm import FakeLLM
        from .supply_chain_fixture import metric_baselines, parse_supply_chain
        from .db import seed_ferry_prices
        store = load_graph(conn)
        _llm = llm or FakeLLM()
        _vector = vector or FakeVector()
        for doc_id, text in parse_supply_chain().policy_docs:
            _vector.add(doc_id, text)
        seed_metrics(conn, metric_baselines())
        seed_ferry_prices(conn)
        for nid in store.all_ids():
            node = store.get_node(nid)
            if node.data_binding:
                node.data_binding.raw_values.update(read_metrics(conn, nid))
        eng = Engine(store, _llm, _vector, generate_report)
    else:
        store, eng, _ = seed_fn(llm=llm, vector=vector, conn=conn, assess=False)
        if persist:
            save_graph(store, conn)

    triggers = TriggerEngine(store)
    poller = PollService(store, conn, eng, triggers)

    # Lazy initial assessment: boot stays fast (no LLM calls). Assessment runs on
    # the first request that needs it, and on data changes.
    _boot = {"assessed": False}
    _dirty = set()   # node ids edited since the last assessment

    def _assess_all():
        """Re-assess only the nodes affected by a change: refresh readings, find
        which leaves changed (plus any never-assessed nodes), and re-score that
        influence cone. Unchanged branches keep their severity. With real DeepSeek
        the cone is flattened into ONE call; with FakeLLM a deterministic cone
        crawl is used (hermetic tests). Robust: a bad LLM entry or query never
        500s -- it is skipped or falls back to the rule crawl."""
        from .models import TriggerType
        changed = poller.refresh()   # leaf ids whose SQL readings changed (resilient)
        dirty = set(changed) | set(_dirty) | {nid for nid in store.all_ids()
                                              if store.get_node(nid).current is None}
        if not dirty:
            _boot["assessed"] = True
            return                   # nothing changed -> nothing re-scored
        scope = store.closure(dirty)

        if getattr(getattr(eng, "llm", None), "model", "fake") == "fake":
            eng.run_crawl(list(dirty), TriggerType.NODE_FIRED)
        else:
            from .flatten import FlattenEngine, assessment_from_batch, input_snapshot, history_match
            from .reports import static_report
            from .db import record_history
            flat = FlattenEngine(store, eng.vector, conn)
            system, messages = flat.build_batch_prompt(scope=scope)
            try:
                raw = eng.llm.batch_assess(system, messages[0]["content"])
                entries = flat.parse_batch_response(raw)
            except Exception:
                eng.run_crawl(list(dirty), TriggerType.NODE_FIRED)   # deterministic fallback
                _dirty.clear()
                _boot["assessed"] = True
                _persist()
                return
            applied = []
            for entry in entries:
                nid = entry.get("node_id")
                if nid in store.nodes and nid in scope:
                    try:
                        node = store.get_node(nid)
                        node.current = assessment_from_batch(node, entry)
                        # Deterministic anchoring: if the current input severities
                        # exactly match a past judgment, override the LLM's score.
                        snap = input_snapshot(store, nid)
                        match = history_match(conn, nid, snap)
                        if match:
                            sev = Severity(match["severity"])
                            score = float(match["score"])
                            node.current.llm_verdict.final_score = score
                            node.current.llm_verdict.severity = sev
                            node.current.llm_verdict.rationale = (
                                f"[anchored] {match.get('rationale', '')}")
                            node.current.signal.score = score
                            node.current.signal.severity = sev
                            node.current.signal.cause = node.current.llm_verdict.rationale
                        node.history.append(node.current.version)
                        node.outbound_signal = node.current.signal
                        applied.append(node)
                    except Exception:
                        continue     # skip a malformed entry rather than fail the request
            for node in applied:
                try:
                    static_report(node, store)
                except Exception:
                    continue
            # Record each node's (inputs -> severity) judgment now that every
            # node in the batch has its fresh signal -- anchors future scoring.
            for node in applied:
                try:
                    record_history(conn, node.id, input_snapshot(store, node.id),
                                   node.current.llm_verdict.severity.value,
                                   node.current.llm_verdict.final_score,
                                   node.current.llm_verdict.rationale)
                except Exception:
                    continue
        _dirty.clear()
        _boot["assessed"] = True
        _persist()

    def _ensure_assessed():
        if not _boot["assessed"]:
            _assess_all()

    def _persist():
        if persist:
            try:
                save_graph(store, conn)
            except Exception:
                pass   # a transient DB write issue must not fail the request

    app = FastAPI(title="4sight")
    app.state.sockets = []
    if WEB.exists():
        app.mount("/static", StaticFiles(directory=str(WEB)), name="static")

    def _broadcast(changed):
        import anyio
        for ws in list(app.state.sockets):
            try:
                anyio.from_thread.run(ws.send_json, {"changed": changed})
            except Exception:
                pass
    eng.listeners.append(_broadcast)

    def _band_value(node, severity_str):
        """Pick a (field, value) that drives `node`'s real structured field into
        the given severity band. LOW resets to a healthy value. Falls back to the
        generic effect_score ladder if the real field has no rule for the level."""
        from .models import Severity as Sev
        GENERIC = {"effect_score", "capacity_drop_pct", "single_owner", "data_age_h"}
        binding = node.data_binding
        real = [r for r in binding.field_rules
                if r.kind == "structured" and r.field not in GENERIC]
        try:
            sev = Sev(severity_str)
        except ValueError:
            sev = Sev.HIGH
        if real:
            field = real[0].field
            on_field = [r for r in real if r.field == field]
            op = on_field[0].operator
            by_sev = {r.severity_on_breach: r.expected for r in on_field}
            if op == "<":  # lower is worse
                if sev == Sev.LOW:
                    return field, max(by_sev.values()) + 20
                if sev in by_sev:
                    return field, by_sev[sev] - 5
            else:          # higher is worse
                if sev == Sev.LOW:
                    return field, min(by_sev.values()) - 20
                if sev in by_sev:
                    return field, by_sev[sev] + 5
        # Fallback: generic effect_score ladder (>=25 MEDIUM, >=50 HIGH, >=75 CRITICAL)
        return "effect_score", {"low": 0.0, "medium": 30.0, "high": 60.0, "critical": 90.0}[sev.value]

    @app.get("/", response_class=HTMLResponse)
    @app.get("/builder", response_class=HTMLResponse)
    def index():
        f = WEB / "builder.html"
        return f.read_text() if f.exists() else "<h1>4sight (web not built yet)</h1>"

    @app.post("/builder/batch-assess")
    def builder_batch_assess(body: dict):
        from .flatten import FlattenEngine, assessment_from_batch
        from .llm import DeepSeekLLM, FakeLLM
        from .rules import score_leaf
        mode = body.get("mode", "full")

        # Rule-based pre-check: evaluate field rules on leaf data sources.
        violations = []
        for nid in store.all_ids():
            node = store.get_node(nid)
            if node.data_binding and node.data_binding.field_rules:
                result = score_leaf(node)
                for b in result.inputs.get("breached", []):
                    violations.append(
                        f"VIOLATION: {node.title} ({b['field']}={b['value']} "
                        f"{b['operator']} {b['expected']}) -> {b['severity']}"
                    )

        flatten = FlattenEngine(store)
        try:
            llm = DeepSeekLLM()
        except Exception:
            llm = FakeLLM()
        system, messages = flatten.build_batch_prompt(mode=mode)

        # Inject violations into the prompt
        if violations:
            violation_text = "\n".join(violations)
            messages[0]["content"] = (
                "The following rule-based threshold violations were detected "
                "before assessment. Factor these into your risk scoring.\n\n"
                + violation_text + "\n\n" + messages[0]["content"]
            )

        try:
            raw = llm.batch_assess(system, messages[0]["content"])
            assessments = flatten.parse_batch_response(raw)
        except Exception:
            raw = FakeLLM().batch_assess(system, messages[0]["content"])
            assessments = flatten.parse_batch_response(raw)
        for entry in assessments:
            node = store.get_node(entry["node_id"])
            node.current = assessment_from_batch(node, entry)
            node.history.append(node.current.version)
            node.outbound_signal = node.current.signal
            node.delta_accumulator = 0.0
        for entry in assessments:
            node = store.get_node(entry["node_id"])
            eng.report_fn(node, store, llm)
        _boot["assessed"] = True  # batch-assess is itself a full assessment pass
        _persist()
        return {"assessments": assessments, "violations": violations}

    @app.post("/builder/nodes/{node_id}/raw-values")
    def set_raw_values(node_id: str, body: dict):
        """Set raw field values on a leaf node to simulate data changes."""
        if node_id not in store.nodes:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=404, content={"error": "node not found"})
        node = store.get_node(node_id)
        if not node.data_binding:
            return {"error": "not a leaf node"}
        raw_values = body.get("raw_values", {})
        for k, v in raw_values.items():
            node.data_binding.raw_values[k] = float(v) if v is not None else None
        _persist()
        return {"node_id": node_id, "raw_values": node.data_binding.raw_values}

    @app.post("/inject/{node_id}")
    def inject(node_id: str, body: dict):
        """Inject a problem at a given severity. Writes a value into the leaf's
        real SQL metric so the refresh drives it into that severity band, then
        re-assesses the whole graph in one flattened batch call. severity=low
        resets to the healthy baseline."""
        if node_id not in store.nodes:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=404, content={"error": "node not found"})
        node = store.get_node(node_id)
        if not node.data_binding:
            return {"error": "not a leaf node"}
        severity = body.get("severity", "high")
        field, value = _band_value(node, severity)
        set_metric(conn, node_id, field, float(value))
        node.data_binding.raw_values[field] = float(value)
        _dirty.add(node_id)
        _assess_all()                 # re-scores this node's cone
        return {"node_id": node_id, "field": field, "value": value,
                "severity": severity, "changed": store.all_ids()}

    @app.post("/poll")
    def poll_sources(body: dict):
        node_ids = body.get("node_ids")
        poller.refresh(node_ids)
        _assess_all()
        return {"changed": store.all_ids()}

    @app.post("/node/{node_id}/readings")
    def set_readings(node_id: str, body: dict):
        """Manually set a leaf's current readings. Persisted to leaf_metrics (an
        override that survives re-reads) so changing a reading is how you inject
        a problem. Does NOT re-assess -- the UI then prompts 'Run Assessment'."""
        if node_id not in store.nodes:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=404, content={"error": "not found"})
        node = store.get_node(node_id)
        if not node.data_binding:
            return {"error": "not a leaf node"}
        for field, value in (body.get("readings") or {}).items():
            if value is None:
                continue
            set_metric(conn, node_id, field, float(value))
            node.data_binding.raw_values[field] = float(value)
        _dirty.add(node_id)   # re-score this node's cone on the next assessment
        _persist()
        return {"node_id": node_id, "raw_values": node.data_binding.raw_values}

    @app.post("/assess")
    def assess_now():
        """Run a full assessment: refresh every node's readings from its data
        source, then one flattened LLM pass. Returns the updated graph."""
        _assess_all()
        return get_builder_graph()

    @app.post("/test-query")
    def test_query(body: dict):
        """Run a read-only SELECT and return its (field, value) readings, so a
        new data-source query can be tested before the node is created."""
        q = (body.get("query") or "").strip()
        if not q.lower().startswith("select"):
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=400, content={"error": "only SELECT queries are allowed"})
        try:
            rows = conn.execute(q).fetchall()
        except Exception as exc:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=400, content={"error": str(exc)})
        readings = {}
        for row in rows:
            if len(row) >= 2 and row[1] is not None:
                try:
                    readings[str(row[0])] = float(row[1])
                except (TypeError, ValueError):
                    readings[str(row[0])] = row[1]
        return {"readings": readings, "rows": [list(r) for r in rows]}

    @app.get("/node/{node_id}/context")
    def node_context(node_id: str):
        """Lazily generate (and cache) an LLM context summary grounded in a
        Chroma vector search over the policy/context docs."""
        if node_id not in store.nodes:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=404, content={"error": "not found"})
        node = store.get_node(node_id)
        if not node.context_summary:
            query = f"{node.title} {node.description}".strip()
            chunks = eng.vector.query_texts(query, k=3)
            summarizer = context_llm or eng.llm
            node.context_summary = summarizer.summarize(node, chunks)
        return {"node_id": node_id, "summary": node.context_summary}

    @app.get("/graph-data")
    def graph_data(role: str = "reviewer"):
        _ensure_assessed()
        viewer = Viewer(id="anon", role=Role(role))
        nodes = {}
        for nid in store.all_ids():
            node = store.get_node(nid)
            entry = {
                "id": nid,
                "title": node.title,
                "kind": node.kind.value,
                "children": store.children(nid),
                "dependencies": [d for d in store.dependencies(nid)],
                "severity": None,
            }
            if node.current:
                entry["severity"] = node.current.llm_verdict.severity.value
            nodes[nid] = entry
        return nodes

    @app.get("/report/{node_id}")
    def report(node_id: str, role: str = "reviewer"):
        _ensure_assessed()
        rep = get_report_fn(node_id, store, Viewer(id="anon", role=Role(role)))
        return rep.model_dump(mode="json") if rep else None

    @app.get("/root")
    def get_root():
        # The root is the top of the influence graph: signals flow into it but
        # it flows nowhere downstream (no influence successors).
        for nid in store.all_ids():
            if not store.influence_successors(nid):
                return {"node_id": nid}
        return {"node_id": store.all_ids()[0] if store.all_ids() else ""}

    @app.get("/trace/{node_id}")
    def trace(node_id: str):
        _ensure_assessed()
        t = trace_fn(node_id, store)
        return {"path": t["path"], "origin": t["origin"].model_dump(mode="json") if t["origin"] else None}

    @app.post("/simulate-change")
    def simulate(body: dict):
        _ensure_assessed()
        now = datetime.now(timezone.utc)
        kind = body.get("kind", "leave")
        source = body.get("source", "Leave Calendar")
        node_id = body.get("node_id", "alice_owner")
        effect_score = float(body.get("effect_score", 40))

        if kind == "salary":
            change = ChangeEvent(source="Payroll (redacted)", record_ref="comp_pool", before=None,
                                 after={"effect_score": effect_score, "category": "compensation"},
                                 at=now, sensitivity=Sensitivity.CONFIDENTIAL)
        elif kind == "leave":
            change = ChangeEvent(source="Personnel Change", record_ref="redacted",
                                 before=None,
                                 after={"effect_score": effect_score,
                                        "capacity_drop_pct": effect_score,
                                        "single_owner": True, "data_age_h": 2},
                                 at=now, sensitivity=Sensitivity.CONFIDENTIAL)
        else:
            change = ChangeEvent(source=source, record_ref=node_id, before=None,
                                 after={"effect_score": effect_score}, at=now,
                                 sensitivity=Sensitivity.INTERNAL)
        eng.on_data_change(node_id, change)
        changed = eng.fire_node(node_id)
        _persist()
        return {"changed": changed}

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        app.state.sockets.append(socket)
        try:
            while True:
                await socket.receive_text()
        except WebSocketDisconnect:
            app.state.sockets.remove(socket)

    # --- Graph Builder endpoints ---

    @app.get("/builder/nodes/{node_id}")
    def get_builder_node(node_id: str):
        if node_id not in store.nodes:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=404, content={"error": "not found"})
        _ensure_assessed()
        node = store.get_node(node_id)
        return {
            "id": node.id, "kind": node.kind.value, "title": node.title,
            "description": node.description,
            "delta_accumulator": node.delta_accumulator,
            "children": store.children(node_id),
            "parents": store.parents(node_id),
            "dependencies": store.dependencies(node_id),
            "dependents": store.dependents(node_id),
            # Direction-correct relationships based on the influence graph:
            # inputs = what flows into this node (what it depends on);
            # consumers = what this node flows into (what depends on it).
            "inputs": store.influence_predecessors(node_id),
            "consumers": store.influence_successors(node_id),
            "context_summary": node.context_summary,
            "severity": node.current.llm_verdict.severity.value if node.current else None,
            "adapter_id": node.data_binding.adapter_id if node.data_binding else "",
            "query": node.data_binding.query if node.data_binding else "",
            # Hide the internal generic effect_score ladder; show only the real
            # graded field rules (at most one per severity band).
            "field_rules": [
                fr.model_dump(mode="json")
                for fr in (node.data_binding.field_rules if node.data_binding else [])
                if fr.field not in {"effect_score", "capacity_drop_pct", "single_owner", "data_age_h"}
            ],
            "raw_values": node.data_binding.raw_values if node.data_binding else {},
            # Inbound = the signals of this node's INPUTS (influence predecessors),
            # computed live so it is correct regardless of assessment path/order.
            "inbound_signals": [
                store.get_node(pid).outbound_signal.model_dump(mode="json")
                for pid in store.influence_predecessors(node_id)
                if store.get_node(pid).outbound_signal
            ],
            "outbound_signal": node.outbound_signal.model_dump(mode="json") if node.outbound_signal else None,
        }

    @app.post("/builder/nodes")
    def create_node(body: dict):
        from .models import DataBinding, Sensitivity, FieldRule
        nid = body.get("id", body.get("title", "untitled"))
        kind = NodeKind(body.get("kind", "task"))
        binding = None
        if kind == NodeKind.LEAF:
            adapter_id = body.get("adapter_id", "")
            query = body.get("query", "")
            field_rules_raw = body.get("field_rules", [])
            field_rules = []
            for fr in field_rules_raw:
                field_rules.append(FieldRule(
                    field=fr["field"],
                    kind=fr.get("kind", "structured"),
                    operator=fr.get("operator", "<"),
                    expected=float(fr.get("expected", 0)),
                    severity_on_breach=Severity(fr.get("severity_on_breach", "medium")),
                ))
            binding = DataBinding(
                adapter_id=adapter_id, query=query,
                sensitivity=Sensitivity.INTERNAL,
                field_rules=field_rules,
            )
            existing = store.find_duplicate_source(binding)
            if existing:
                return {"id": existing, "deduped": True}
            # Preserve existing readings so a field-rule edit doesn't lose the
            # leaf's current data values (which live in DB + raw_values).
            if nid in store.nodes:
                old = store.get_node(nid)
                if old.data_binding:
                    binding.raw_values = dict(old.data_binding.raw_values)
        is_new = nid not in store.nodes
        node = Node(id=nid, kind=kind, title=body.get("title", nid),
                    description=body.get("description", ""),
                    data_binding=binding)
        if not is_new:
            old = store.get_node(nid)
            node.delta_accumulator = old.delta_accumulator
            node.pending_delta = old.pending_delta
            node.current = old.current
            node.history = old.history
            node.report = old.report
            node.inbound_signals = old.inbound_signals
            node.outbound_signal = old.outbound_signal
            node.raw = old.raw
            node.pending_change = old.pending_change
            if old.data_binding and not binding:
                node.data_binding = old.data_binding
        store.add_node(node)
        _persist()
        return {"id": nid, "deduped": False}

    @app.delete("/builder/nodes/{node_id}")
    def delete_node(node_id: str):
        if node_id in store.nodes:
            store._edges = [e for e in store._edges
                           if e.src != node_id and e.dst != node_id]
            store._infl.remove_node(node_id)
            del store.nodes[node_id]
        _persist()
        return {"deleted": node_id}

    @app.post("/builder/edges")
    def create_edge(body: dict):
        weight_str = body.get("weight", "medium")
        try:
            weight = Severity(weight_str)
        except ValueError:
            weight = Severity.MEDIUM
        try:
            store.add_edge(body["src"], body["dst"], EdgeType(body["type"]), weight)
            _persist()
            return {"src": body["src"], "dst": body["dst"], "type": body["type"], "weight": weight.value}
        except ValueError as exc:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=400, content={"error": str(exc)})

    @app.delete("/builder/edges")
    def delete_edge(body: dict):
        etype = EdgeType(body["type"])
        store._edges = [e for e in store._edges
                        if not (e.src == body["src"] and e.dst == body["dst"] and e.type == etype)]
        if etype == EdgeType.DECOMPOSITION:
            u, v = body["dst"], body["src"]
        else:
            u, v = body["src"], body["dst"]
        if store._infl.has_edge(u, v):
            store._infl.remove_edge(u, v)
        _persist()
        return {"deleted": True}

    @app.get("/builder/graph")
    def get_builder_graph():
        nodes = []
        for nid in store.all_ids():
            n = store.get_node(nid)
            nodes.append({
                "id": nid, "kind": n.kind.value, "title": n.title,
                "description": n.description,
                "severity": n.current.llm_verdict.severity.value if n.current else None,
                "delta_accumulator": n.delta_accumulator,
                "field_rules": [fr.model_dump(mode="json") for fr in (n.data_binding.field_rules if n.data_binding else [])],
                "raw_values": n.data_binding.raw_values if n.data_binding else {},
                "outbound_signal": n.outbound_signal.model_dump(mode="json") if n.outbound_signal else None,
            })
        edges = [{"src": e.src, "dst": e.dst, "type": e.type.value, "weight": e.weight.value}
                 for e in store._edges]
        return {"nodes": nodes, "edges": edges}

    return app
