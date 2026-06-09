try:
    from dotenv import load_dotenv
    load_dotenv()  # pick up DEEPSEEK_API_KEY from .env for the real demo
except Exception:
    pass

from .api import build_app
from .seed import load_supply_chain


def _llm():
    """Real DeepSeek (deepseek-v4-flash by default; override via DEEPSEEK_MODEL).
    Falls back to the deterministic FakeLLM if unavailable."""
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
# real Chroma vector search, and real DeepSeek for both risk scoring and the
# lazy unstructured context summaries. (Engine assessment now uses DeepSeek, so
# boot and inject run live LLM calls; thinking budgets are trimmed to keep it
# responsive.)
app = build_app(seed_fn=load_supply_chain, db_path="foursight.db",
                llm=_llm(), vector=_vector())
