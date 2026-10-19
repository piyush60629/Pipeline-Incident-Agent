import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from incident_agent.knowledge import KnowledgeBase, set_default_knowledge_base  # noqa: E402


@pytest.fixture(autouse=True)
def keyword_only_knowledge_base(monkeypatch):
    """Tests never call Gemini: use the keyword-only knowledge base."""

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    set_default_knowledge_base(KnowledgeBase(embedder=None))
    yield
    set_default_knowledge_base(None)
