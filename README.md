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

## Run the app (Fab 17 demo)

The real app serves the Fab 17 semiconductor supply-chain graph from the
committed SQLite demo DB (`foursight.db`). It uses real DeepSeek scoring when
`DEEPSEEK_API_KEY` is set in `.env`, and the deterministic FakeLLM otherwise:

```
uvicorn foursight.demo:app --port 8000
# open http://localhost:8000
```

A fresh in-memory variant (no persistence, re-seeded from the supply-chain
fixture on each boot) is available via the app factory and also serves Fab 17:

```
uvicorn foursight.api:build_app --factory --reload --port 8000
```

To explore the UI against lightweight fakes only (no LLM, no DB):

```
uvicorn foursight.mock_server:app --port 8001
```

## Demo script

Driven from the web UI (Builder and Report tabs):

1. **Qualitative human risk:** as privileged, open Alice Chen (a confidential human leaf), add a qualitative rule `Taking leave -> critical`, and Run Assessment. Alice goes critical and the risk propagates up Lithography and Packaging to the Fab 17 root. As a reviewer her underlying data is redacted to severity only.
2. **Structured data change:** edit a data-source reading (e.g. SUMCO yield drops to 40), Run Assessment, and watch the affected cone re-score while unrelated branches hold steady; the root report traces each driver back to its origin.

Telegram alerts: set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env` to get a push whenever the Fab 17 root severity escalates.

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
├── seed.py             # load_supply_chain() (Fab 17) + legacy build_seed()/load_company()
├── notify.py           # Telegram push on root-severity escalation
├── testkit.py          # Random DAG generator
├── api.py              # FastAPI app (injectable seams); default seed is Fab 17
├── mcp_server.py       # MCP tools over the store seam
├── demo.py             # Demo entrypoint (Fab 17 supply chain, persisted DB)
├── web/
│   ├── builder.html    # Two-tab UI: Builder canvas + Report
│   ├── builder.js
│   └── builder.css
├── ingestion/
│   ├── base.py         # SourceAdapter ABC
│   ├── csv_adapter.py  # CsvLeaveAdapter
│   └── payroll_redacted.py  # PayrollRedactedAdapter
└── fixtures/
    ├── supply_chain/   # Fab 17 demo: topology.json (20 nodes) + policies/
    └── mock_company/   # legacy build_seed/load_company graph
```

## License

MIT
