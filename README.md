# 4sight

Operational continuity risk assessment. Models an organization as a DAG of tasks and dependencies, reacts to data-source changes, and reasons about their impact to produce navigable risk reports.

## The loop

```
data source changes -> node updates -> rules + LLM re-assess -> change propagates up and sideways -> top-level report refreshes -> risk traces back to the originating change
```

## Architecture

One Python package (`foursight`). A shared Foundation defines the data models, interface seams, and test doubles. Streams build against those seams: Core (graph + engine), MCP (tool layer), Frontend API (FastAPI), and Frontend Web (report viewer).

## Tech stack

Python 3.11+, Pydantic v2, NetworkX, ChromaDB, Anthropic SDK (against DeepSeek's Anthropic-compatible endpoint), FastMCP, FastAPI

## Quick start

```
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# set DEEPSEEK_API_KEY in .env (optional, FakeLLM works without it)
pytest
```

## Run the mock server

```
uvicorn foursight.mock_server:app --port 8001
# open http://localhost:8001
```

## Run the real app

```
uvicorn foursight.api:build_app --factory --reload
# open http://localhost:8000
```

## Demo script

Two acts, both driven from the web UI or via `POST /simulate-change`:

1. **Leave act:** key owner files leave, leaf change propagates, root report flips medium to high, traceable to the Leave Calendar origin.
2. **Sensitivity act:** a salary hike fires through the redacted payroll adapter; budget effect is visible to reviewers but the source figure stays hidden. Switch the role selector to privileged to see the full report.

## Project structure

```
src/foursight/
├── models.py           # 12 Pydantic models, 6 enums
├── fakes.py            # Test doubles (FakeStore, FakeEngine)
├── mock_server.py      # Standalone FastAPI for web dev
├── company_fixture.py  # Mock company parser
├── graph_store.py      # NetworkX DAG, acyclic enforcement
├── rules.py            # Deterministic rule scoring
├── sensitivity.py      # 4-level sensitivity + declassification
├── llm.py              # FakeLLM + DeepSeekLLM
├── vector_store.py     # FakeVector + ChromaVectorStore
├── assess.py           # Node assessment: leaf field rules + LLM, non-leaf weighted signal synthesis
├── propagation.py      # Topological crawl (every changed signal propagates in demo mode)
├── reports.py          # Per-node cached reports + trace-to-source
├── seed.py             # build_seed() + load_company()
├── testkit.py          # Random DAG generator
├── api.py              # FastAPI app (injectable seams)
├── mcp_server.py       # MCP tools over the store seam
├── demo.py             # Demo entrypoint (load_company)
├── web/
│   ├── index.html
│   └── app.js
├── ingestion/
│   ├── base.py         # SourceAdapter ABC
│   ├── csv_adapter.py  # CsvLeaveAdapter
│   └── payroll_redacted.py  # PayrollRedactedAdapter
└── fixtures/mock_company/
    ├── topology.json   # 12 nodes, 13 edges
    └── policies/
        ├── bcp.md
        └── leave_policy.md
```

## License

MIT
