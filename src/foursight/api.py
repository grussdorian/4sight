from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from .models import ChangeEvent, Sensitivity, Viewer, Role, Node, NodeKind, EdgeType, Severity

WEB = Path(__file__).parent / "web"


def build_app(seed_fn=None, get_report_fn=None, trace_fn=None) -> FastAPI:
    if seed_fn is None:
        from .seed import build_seed as seed_fn
    if get_report_fn is None:
        from .reports import get_report as get_report_fn
    if trace_fn is None:
        from .reports import trace_to_source as trace_fn

    store, eng, _ = seed_fn()
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
        from .flatten import FlattenEngine
        from .llm import DeepSeekLLM, FakeLLM
        mode = body.get("mode", "full")

        # Rule-based pre-check: evaluate threshold_rules on leaf data sources
        violations = []
        for nid in store.all_ids():
            node = store.get_node(nid)
            if node.data_binding and node.data_binding.threshold_rules:
                for rule in node.data_binding.threshold_rules:
                    raw_val = node.data_binding.raw_value
                    if raw_val is None:
                        continue
                    field = rule.get("field", "")
                    op = rule.get("operator", "<")
                    threshold = float(rule.get("value", 0))
                    if op == "<" and raw_val < threshold:
                        violations.append(
                            f"VIOLATION: {node.title} ({field}={raw_val}) "
                            f"is below threshold {threshold}"
                        )
                    elif op == ">" and raw_val > threshold:
                        violations.append(
                            f"VIOLATION: {node.title} ({field}={raw_val}) "
                            f"exceeds threshold {threshold}"
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
        for a in assessments:
            node = store.get_node(a["node_id"])
            node.current = a
            node.delta_accumulator = 0.0
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
        return {"node_id": node_id, "raw_values": node.data_binding.raw_values}

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
        return {"changed": eng.fire_node(node_id)}

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
        return {"id": nid, "deduped": False}

    @app.delete("/builder/nodes/{node_id}")
    def delete_node(node_id: str):
        if node_id in store.nodes:
            store._edges = [e for e in store._edges
                           if e.src != node_id and e.dst != node_id]
            store._infl.remove_node(node_id)
            del store.nodes[node_id]
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
