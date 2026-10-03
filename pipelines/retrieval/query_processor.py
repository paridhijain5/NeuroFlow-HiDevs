"""Step 1: query expansion, metadata-filter extraction, query-type classification (one LLM call)."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

QUERY_TYPES = ("factual", "analytical", "comparative", "procedural")
MAX_EXPANSIONS = 3

PROMPT = """You prepare search queries for a retrieval system. Respond with ONLY a JSON object:
{{"expansions": [...], "filters": {{...}}, "query_type": "..."}}

- "expansions": 2-3 alternative phrasings of the query that use different words (synonyms, related terms).
  Example: for "how does attention work in transformers?" -> ["explain self-attention mechanism", "transformer attention weights calculation"]
- "filters": ONLY explicit constraints stated in the query, as flat key/value pairs.
  Example: "Show me documents from 2023 about climate change" -> {{"year": 2023, "topic": "climate"}}
  Use {{}} when the query states no such constraint. Never invent filters.
- "query_type": one of factual, analytical, comparative, procedural.

Query: {query}"""

HYDE_PROMPT = ("Write a short, plausible passage (3-4 sentences) that would directly answer the question below, "
               "as it might appear in a technical document. Do not mention that it is hypothetical.\n\n"
               "Question: {query}")

_YEAR = re.compile(r"\b(19|20)\d{2}\b")


@dataclass
class ProcessedQuery:
    original: str
    expansions: list[str] = field(default_factory=list)
    filters: dict = field(default_factory=dict)
    query_type: str = "factual"
    hyde_text: str | None = None

    @property
    def all_queries(self) -> list[str]:
        return [self.original, *self.expansions]


def heuristic_query_type(query: str) -> str:
    q = query.lower()
    if re.search(r"\b(vs\.?|versus|compare|compared|difference between|differences)\b", q):
        return "comparative"
    if re.search(r"\b(how (do|to|can|should)|steps? to|guide|tutorial|set up|install)\b", q):
        return "procedural"
    if re.search(r"\b(why|analy[sz]e|impact|effect|trend|implication|cause)\b", q):
        return "analytical"
    return "factual"


def _parse(raw: str) -> dict:
    match = re.search(r"\{.*\}", raw, re.DOTALL)  # tolerate code fences / chatter
    if not match:
        raise ValueError("no JSON object in LLM output")
    return json.loads(match.group(0))


class QueryProcessor:
    def __init__(self, client):
        self.client = client

    async def process(self, query: str) -> ProcessedQuery:
        from providers.base import ChatMessage
        from providers.router import RoutingCriteria

        data: dict = {}
        try:
            result = await self.client.chat([ChatMessage("user", PROMPT.format(query=query))],
                                            RoutingCriteria(task_type="classification"),
                                            temperature=0, max_tokens=300)
            data = _parse(result.content)
        except Exception as exc:  # never let query preprocessing break retrieval
            log.warning("Query processing via LLM failed (%s); using heuristics", exc)

        seen = {query.strip().lower()}
        expansions: list[str] = []
        for e in data.get("expansions") or []:
            if isinstance(e, str) and e.strip() and e.strip().lower() not in seen:
                seen.add(e.strip().lower())
                expansions.append(e.strip())
        expansions = expansions[:MAX_EXPANSIONS]

        filters = {}
        raw_filters = data.get("filters")
        if isinstance(raw_filters, dict):
            filters = {str(k): v for k, v in list(raw_filters.items())[:5]
                       if isinstance(v, (str, int, float, bool)) and v != ""}
        if "year" not in filters and (m := _YEAR.search(query)):
            filters["year"] = int(m.group(0))  # deterministic backstop for explicit years

        qtype = data.get("query_type")
        if qtype not in QUERY_TYPES:
            qtype = heuristic_query_type(query)
        return ProcessedQuery(original=query, expansions=expansions, filters=filters, query_type=qtype)

    async def generate_hyde(self, query: str) -> str | None:
        """HyDE: a hypothetical answer whose embedding is used instead of the raw query's."""
        from providers.base import ChatMessage
        from providers.router import RoutingCriteria

        try:
            result = await self.client.chat([ChatMessage("user", HYDE_PROMPT.format(query=query))],
                                            RoutingCriteria(task_type="classification"),
                                            temperature=0.3, max_tokens=200)
            return result.content.strip() or None
        except Exception as exc:
            log.warning("HyDE generation failed: %s", exc)
            return None
