"""Curated knowledge base search. Deterministic keyword scoring - no embeddings, no vector DB."""

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_KB_PATH = Path(__file__).parent / "data" / "knowledge_base.json"
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "a an and are as at be but by can do does for from how i in is it me my of on or the this to "
    "what when where which who will with you your have has need want about get there any".split()
)

CATEGORIES: tuple[str, ...] = ("services", "faq", "safety", "handoff", "limitations", "how_to")


@lru_cache
def load_kb() -> dict[str, Any]:
    return json.loads(_KB_PATH.read_text(encoding="utf-8"))


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1}


def _score(query_tokens: set[str], query_text: str, entry: dict[str, Any]) -> float:
    title_tokens = _tokens(entry["title"])
    keyword_tokens = set().union(*(_tokens(k) for k in entry["keywords"])) if entry["keywords"] else set()
    content_tokens = _tokens(entry["content"])
    score = 0.0
    score += 3.0 * len(query_tokens & title_tokens)
    score += 2.0 * len(query_tokens & keyword_tokens)
    score += 0.5 * len(query_tokens & content_tokens)
    lowered = query_text.lower()
    # multi-word keyword phrases ("safety key", "how long") are a strong signal
    score += 3.0 * sum(1 for k in entry["keywords"] if " " in k and k in lowered)
    return score


def _public(entry: dict[str, Any], score: float) -> dict[str, Any]:
    return {
        "id": entry["id"],
        "category": entry["category"],
        "title": entry["title"],
        "content": entry["content"],
        "source_type": entry["source_type"],
        "source_url": entry["source_url"] or None,
        "retrieved": entry["retrieved"],
        "score": round(score, 2),
    }


def search(query: str, categories: tuple[str, ...] | None = None, limit: int = 3) -> list[dict[str, Any]]:
    """Return up to ``limit`` entries with a positive relevance score, best first."""
    q_tokens = _tokens(query)
    if not q_tokens:
        return []
    scored = []
    for entry in load_kb()["entries"]:
        if categories and entry["category"] not in categories:
            continue
        s = _score(q_tokens, query, entry)
        if s >= 2.0:  # require at least a keyword-level match, not a stray content word
            scored.append((s, entry))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [_public(e, s) for s, e in scored[:limit]]


def entries_in(category: str) -> list[dict[str, Any]]:
    return [_public(e, 0.0) for e in load_kb()["entries"] if e["category"] == category]
