try:
    from dotenv import load_dotenv
    load_dotenv()  # pick up DEEPSEEK_API_KEY from .env for the real demo
except Exception:
    pass

from .api import build_app
from .seed import load_supply_chain


def _context_llm():
    """Real DeepSeek for the lazy, on-click unstructured context summaries.
    Falls back to the deterministic FakeLLM summary if unavailable."""
    try:
        from .llm import DeepSeekLLM
        return DeepSeekLLM()
    except Exception:
        return None


def _vector():
    """Real local Chroma for vector search over the policy/context docs."""
    try:
        from .vector_store import ChromaVectorStore
        return ChromaVectorStore()
    except Exception:
        return None


# Fab 17 supply-chain demo: SQLite-backed (foursight.db), real SQL polling,
# real Chroma vector search, and real DeepSeek context summaries (lazy, on
# click). Risk scoring stays on the fast, deterministic FakeLLM so inject and
# the cascade update instantly; the unstructured summaries use DeepSeek.
app = build_app(seed_fn=load_supply_chain, db_path="foursight.db",
                vector=_vector(), context_llm=_context_llm())
