"""Tool Router: keep the per-request tool list inside the context budget.

The 9B model runs at 16k context, so tool definitions must stay small:
at most ``max_tools`` schemas (default 40) and about ``max_tokens``
(default 4000) estimated tokens of tool definitions per request.

Built-in tools are always present. Extension tools (MCP servers and
connector-backed MCP servers) are ranked by relevance to the user's
message plus the last two turns:

* lexical score — token overlap against the tool's name parts, its
  server's name, its description, and its parameter names;
* embedding score — only when an embedding provider is available
  (VISION probes the local endpoint's ``/v1/embeddings``); cosine
  similarity blended 50/50 with the lexical score.

Tools are added best-first until the tool-count cap or the token cap is
reached, or the score falls below ``min_score``. With routing disabled
the same ranking applies but there is no score threshold — only the
caps. Every selection returns a report so the UI can show the budget
("12/40 tools, ~1.8k tokens") and logs can record what was sent.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # avoid a registry <-> router import cycle at runtime
    from .registry import ToolDefinition

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "a", "an", "the", "and", "or", "to", "of", "in", "on", "for", "with",
    "me", "my", "i", "you", "please", "is", "are", "it", "this", "that",
    "show", "get", "give", "tell", "can", "could", "would", "do", "does",
    "what", "when", "how", "from", "into", "as", "at", "by", "be", "use",
    "using", "make", "then", "than", "too", "also", "about",
}

# Callable[[list[str]], list[list[float]] | None]
EmbeddingProvider = Callable[[list[str]], Any]


def tokenize(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(str(text or "").lower())
        if len(token) > 1 and token not in _STOPWORDS
    }


def estimate_tokens(schema: dict[str, Any]) -> int:
    """Rough token estimate for one serialized tool schema (~4 chars/token)."""
    try:
        return max(1, math.ceil(len(json.dumps(schema, ensure_ascii=False)) / 4))
    except Exception:
        return 64


def _name_parts(name: str) -> set[str]:
    parts: set[str] = set()
    for chunk in re.split(r"[^a-z0-9]+", str(name or "").lower()):
        if len(chunk) > 1:
            parts.add(chunk)
        # split camelCase-ish concatenations cheaply via vowels is overkill;
        # also index the whole chunk so exact names still match.
    return parts


def lexical_score(query_tokens: set[str], definition: ToolDefinition) -> float:
    """0..1 relevance of one tool definition to the query tokens."""
    if not query_tokens:
        return 0.0
    server_hint = definition.source[4:] if definition.source.startswith("mcp:") else ""
    weighted: list[tuple[set[str], float]] = [
        (_name_parts(definition.name), 3.0),
        (_name_parts(server_hint), 2.0),
        (tokenize(definition.description), 1.5),
    ]
    schema = definition.parameters or {}
    if isinstance(schema, dict):
        props = schema.get("properties") or {}
        if isinstance(props, dict):
            weighted.append((_name_parts(" ".join(str(k) for k in props)), 1.0))
    hits = 0.0
    best_possible = 0.0
    for token in query_tokens:
        best = max((weight for tokens, weight in weighted if token in tokens), default=0.0)
        hits += best
        best_possible += 3.0
    if best_possible <= 0:
        return 0.0
    return min(1.0, hits / best_possible)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return (dot / norm) if norm else 0.0


def rank_extension_tools(
    definitions: list[ToolDefinition],
    query: str,
    *,
    embedding_provider: EmbeddingProvider | None = None,
    vector_cache: dict[str, list[float]] | None = None,
) -> list[tuple[ToolDefinition, float]]:
    """Rank extension tool definitions best-first for this query."""
    query_tokens = tokenize(query)
    scores: dict[str, float] = {
        definition.name: lexical_score(query_tokens, definition) for definition in definitions
    }
    if embedding_provider is not None and definitions and query.strip():
        try:
            cache = vector_cache if vector_cache is not None else {}
            missing = [d for d in definitions if d.name not in cache]
            if missing:
                vectors = embedding_provider(
                    [f"{d.name}. {d.description}"[:600] for d in missing]
                )
                if vectors:
                    for definition, vector in zip(missing, vectors, strict=False):
                        if vector:
                            cache[definition.name] = list(vector)
            query_vectors = embedding_provider([query[:1000]])
            query_vector = query_vectors[0] if query_vectors else None
            if query_vector:
                for definition in definitions:
                    vector = cache.get(definition.name)
                    if vector:
                        similarity = max(0.0, _cosine(list(query_vector), vector))
                        scores[definition.name] = 0.5 * scores[definition.name] + 0.5 * similarity
        except Exception:
            logger.debug("Embedding ranking unavailable; lexical only", exc_info=True)
    ranked = sorted(definitions, key=lambda d: (-scores[d.name], d.name))
    return [(definition, round(scores[definition.name], 4)) for definition in ranked]


def select_tool_schemas(
    builtin_schemas: list[dict[str, Any]],
    extension_definitions: list[ToolDefinition],
    query: str,
    *,
    max_tools: int = 40,
    max_tokens: int = 4000,
    min_score: float = 0.08,
    routing_enabled: bool = True,
    embedding_provider: EmbeddingProvider | None = None,
    vector_cache: dict[str, list[float]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Builtins always; extension tools by score until a cap stops us."""
    tools = list(builtin_schemas)
    used_tokens = sum(estimate_tokens(schema) for schema in tools)
    ranked = rank_extension_tools(
        extension_definitions,
        query,
        embedding_provider=embedding_provider,
        vector_cache=vector_cache,
    )
    included: list[str] = []
    dropped: list[dict[str, Any]] = []
    for definition, score in ranked:
        schema = definition.openai_schema()
        cost = estimate_tokens(schema)
        reason = ""
        if len(tools) >= max_tools:
            reason = "tool-count cap"
        elif used_tokens + cost > max_tokens:
            reason = "token cap"
        elif routing_enabled and score < min_score:
            reason = "below relevance threshold"
        if reason:
            dropped.append({"name": definition.name, "score": score, "reason": reason})
            continue
        tools.append(schema)
        used_tokens += cost
        included.append(definition.name)
    report = {
        "included_extension_tools": included,
        "dropped": dropped,
        "tool_count": len(tools),
        "max_tools": max_tools,
        "estimated_tokens": used_tokens,
        "max_tokens": max_tokens,
        "routing_enabled": routing_enabled,
        "builtin_count": len(builtin_schemas),
        "extension_available": len(extension_definitions),
    }
    logger.info(
        "Tool router: %d/%d tools (~%d tokens); extension included=%s dropped=%d",
        len(tools), max_tools, used_tokens, included, len(dropped),
    )
    return tools, report
