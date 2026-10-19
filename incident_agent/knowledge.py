"""Search over runbooks and past incident reports.

Hybrid retrieval:
  * BM25 keyword search (always on). Error codes such as AADSTS7000222 or
    DelimitedTextColumnCountMismatch are exact strings, which keyword search matches well.
  * Gemini embeddings (when GEMINI_API_KEY is set) for meaning-based matches such as
    "credentials ran out" vs "secret expired".
Results are merged with Reciprocal Rank Fusion. The knowledge base is ~50 sections, so a
plain in-memory index is enough; a vector database would add nothing at this size.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Sequence

import numpy as np
from rank_bm25 import BM25Okapi

from .config import GEMINI_EMBEDDING_MODEL, KNOWLEDGE_DIR, env

log = logging.getLogger(__name__)

Source = Literal["all", "runbooks", "incidents"]
RRF_K = 60
STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "was", "with", "by", "be",
    "it", "this", "that", "as", "at", "from", "after", "if", "not", "no", "do", "does", "how", "what", "why",
}


@dataclass(frozen=True)
class Section:
    source: str  # "runbooks" or "incidents"
    document: str  # file name
    title: str  # document title > section heading
    text: str

    @property
    def citation(self) -> str:
        return f"{self.source}/{self.document} > {self.title.split(' > ')[-1]}"


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9_\-.]+", text.lower())
    return [t.strip(".-") for t in tokens if t.strip(".-") and t not in STOPWORDS]


def split_markdown(path: Path, source: str) -> list[Section]:
    """One section per '## ' heading; incident reports are short, so they stay whole."""

    text = path.read_text(encoding="utf-8")
    title_match = re.search(r"^# (.+)$", text, flags=re.MULTILINE)
    title = title_match.group(1).strip() if title_match else path.stem
    if source == "incidents":
        body = re.sub(r"^# .+$", "", text, count=1, flags=re.MULTILINE).strip()
        return [Section(source, path.name, title, f"{title}\n{body}")]

    sections = []
    parts = re.split(r"^## ", text, flags=re.MULTILINE)
    for part in parts[1:]:
        heading, _, body = part.partition("\n")
        sections.append(Section(source, path.name, f"{title} > {heading.strip()}", f"{title} > {heading.strip()}\n{body.strip()}"))
    return sections


def load_sections(knowledge_dir: Path = KNOWLEDGE_DIR) -> list[Section]:
    sections: list[Section] = []
    for source in ("runbooks", "incidents"):
        for path in sorted((knowledge_dir / source).glob("*.md")):
            sections.extend(split_markdown(path, source))
    return sections


Embedder = Callable[[Sequence[str], str], np.ndarray]  # (texts, task_type) -> matrix


def gemini_embedder() -> Embedder | None:
    api_key = env("GEMINI_API_KEY")
    if not api_key:
        return None
    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    client = GoogleGenerativeAIEmbeddings(model=GEMINI_EMBEDDING_MODEL, google_api_key=api_key, output_dimensionality=768)

    def embed(texts: Sequence[str], task_type: str) -> np.ndarray:
        if task_type == "RETRIEVAL_QUERY":
            return np.asarray([client.embed_query(texts[0], task_type=task_type)], dtype=np.float32)
        return np.asarray(client.embed_documents(list(texts), task_type=task_type), dtype=np.float32)

    return embed


def _normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


class KnowledgeBase:
    def __init__(self, sections: list[Section] | None = None, embedder: Embedder | None = None) -> None:
        self.sections = sections if sections is not None else load_sections()
        self.bm25 = BM25Okapi([tokenize(s.text) for s in self.sections])
        self.embedder = embedder
        self._vectors: np.ndarray | None = None
        self._lock = threading.Lock()

    @property
    def mode(self) -> str:
        return "hybrid (BM25 + Gemini embeddings)" if self.embedder else "keyword (BM25)"

    def _section_vectors(self) -> np.ndarray | None:
        if self.embedder is None:
            return None
        with self._lock:
            if self._vectors is None:
                self._vectors = _normalize(self.embedder([s.text for s in self.sections], "RETRIEVAL_DOCUMENT"))
            return self._vectors

    def search(self, query: str, source: Source = "all", k: int = 3) -> list[tuple[Section, float]]:
        allowed = [i for i, s in enumerate(self.sections) if source == "all" or s.source == source]
        if not allowed or not query.strip():
            return []

        bm25_scores = self.bm25.get_scores(tokenize(query))
        # Only sections that share at least one keyword count in the keyword ranking.
        keyword_hits = [i for i in allowed if bm25_scores[i] > 0]
        rankings = [sorted(keyword_hits, key=lambda i: bm25_scores[i], reverse=True)] if keyword_hits else []

        if self.embedder is not None:
            try:
                vectors = self._section_vectors()
                query_vector = _normalize(self.embedder([query], "RETRIEVAL_QUERY"))[0]
                similarity = vectors @ query_vector
                rankings.append(sorted(allowed, key=lambda i: similarity[i], reverse=True))
            except Exception as exc:  # noqa: BLE001 - fall back to keyword search
                log.warning("Embedding search failed, using keyword search only: %s", exc)

        fused: dict[int, float] = {}
        for ranking in rankings:
            for rank, index in enumerate(ranking, start=1):
                fused[index] = fused.get(index, 0.0) + 1.0 / (RRF_K + rank)
        best = sorted(fused, key=fused.get, reverse=True)[:k]
        return [(self.sections[i], round(fused[i], 4)) for i in best]


_default_kb: KnowledgeBase | None = None
_default_lock = threading.Lock()


def default_knowledge_base() -> KnowledgeBase:
    global _default_kb
    with _default_lock:
        if _default_kb is None:
            _default_kb = KnowledgeBase(embedder=gemini_embedder())
        return _default_kb


def set_default_knowledge_base(kb: KnowledgeBase | None) -> None:
    global _default_kb
    with _default_lock:
        _default_kb = kb
