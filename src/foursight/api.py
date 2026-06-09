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
        store = load_graph(conn)
        _llm = llm or FakeLLM()
        _vector = vector or FakeVector()
        for doc_id, text in parse_supply_chain().policy_docs:
            _vector.add(doc_id, text)
        seed_metrics(conn, metric_baselines())
        for nid in store.all_ids():
            node = store.get_node(nid)
            if node.data_binding:
                node.data_binding.raw_values.update(read_metrics(conn, nid))
        eng = Engine(store, _llm, _vector, generate_report)
        eng.run_full()
    else:
        store, eng, _ = seed_fn(llm=llm, vector=vector, conn=conn)
        if persist:
            save_graph(store, conn)

    triggers = TriggerEngine(store)
    poller = PollService(store, conn, eng, triggers)

    def _persist():
        if persist:
            save_graph(store, conn)

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
    def index():
        f = WEB / "index.html"
        return f.read_text() if f.exists() else "<h1>4sight (web not built yet)</h1>"

    @app.get("/graph", response_class=HTMLResponse)
    def graph():
        f = WEB / "graph.html"
        return f.read_text() if f.exists() else "<h1>graph not built yet</h1>"

    @app.get("/builder", response_class=HTMLResponse)
    def builder():
        f = WEB / "builder.html"
        return f.read_text() if f.exists() else "<h1>builder not built yet</h1>"

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
        real SQL metric so the next poll drives it into that severity band, then
        polls + re-assesses the influence cone immediately. severity=low resets."""
        if node_id not in store.nodes:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=404, content={"error": "node not found"})
        node = store.get_node(node_id)
        if not node.data_binding:
            return {"error": "not a leaf node"}
        severity = body.get("severity", "high")
        field, value = _band_value(node, severity)
        set_metric(conn, node_id, field, float(value))
        changed = poller.poll([node_id])
        _persist()
        return {"node_id": node_id, "field": field, "value": value,
                "severity": severity, "changed": changed}

    @app.post("/poll")
    def poll_sources(body: dict):
        node_ids = body.get("node_ids")
        changed = poller.poll(node_ids)
        _persist()
        return {"changed": changed}

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

    @app.get("/raw", response_class=HTMLResponse)
    def raw_graph():
        f = WEB / "raw.html"
        return f.read_text() if f.exists() else "<h1>raw graph not built yet</h1>"

    @app.get("/graph-raw")
    def graph_raw(role: str = "reviewer"):
        viewer = Viewer(id="anon", role=Role(role))
        nodes = []
        for nid in store.all_ids():
            node = store.get_node(nid)
            entry = {
                "id": nid,
                "title": node.title,
                "kind": node.kind.value,
                "severity": None,
            }
            if node.current:
                entry["severity"] = node.current.llm_verdict.severity.value
            nodes.append(entry)
        edges = []
        for nid in store.all_ids():
            node = store.get_node(nid)
            for cid in store.children(nid):
                child = store.get_node(cid)
                edge_obj = next((e for e in store._edges if e.src == nid and e.dst == cid), None)
                edges.append({
                    "src": nid,
                    "dst": cid,
                    "src_title": node.title,
                    "dst_title": child.title,
                    "type": "decomposition",
                    "weight": edge_obj.weight.value if edge_obj else "medium",
                })
            for did in store.dependencies(nid):
                dep = store.get_node(did) if did in store.nodes else None
                edge_obj = next((e for e in store._edges if e.src == did and e.dst == nid), None)
                edges.append({
                    "src": did,
                    "dst": nid,
                    "src_title": dep.title if dep else did,
                    "dst_title": node.title,
                    "type": "dependency",
                    "weight": edge_obj.weight.value if edge_obj else "medium",
                })
        return {"nodes": nodes, "edges": edges}

    @app.get("/graph-data")
    def graph_data(role: str = "reviewer"):
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
        rep = get_report_fn(node_id, store, Viewer(id="anon", role=Role(role)))
        return rep.model_dump(mode="json") if rep else None

    @app.get("/root")
    def get_root():
        for nid in store.all_ids():
            if not store.parents(nid):
                return {"node_id": nid}
        return {"node_id": store.all_ids()[0] if store.all_ids() else ""}

    @app.get("/trace/{node_id}")
    def trace(node_id: str):
        t = trace_fn(node_id, store)
        return {"path": t["path"], "origin": t["origin"].model_dump(mode="json") if t["origin"] else None}

    @app.post("/simulate-change")
    def simulate(body: dict):
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
            "field_rules": [fr.model_dump(mode="json") for fr in (node.data_binding.field_rules if node.data_binding else [])],
            "raw_values": node.data_binding.raw_values if node.data_binding else {},
            "inbound_signals": [s.model_dump(mode="json") for s in node.inbound_signals],
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
        node = Node(id=nid, kind=kind, title=body.get("title", nid),
                    description=body.get("description", ""),
                    data_binding=binding)
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
