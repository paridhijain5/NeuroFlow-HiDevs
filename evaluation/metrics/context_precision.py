from __future__ import annotations
import asyncio
from ..llm import LLM, get_llm
from ._utils import is_yes, clamp01

USEFUL_PROMPT = """[task: passage_useful]
Was this passage useful in generating the answer? Answer with exactly one word: yes or no.
<query>{query}</query>
<answer>{answer}</answer>
<passage>{passage}</passage>"""


async def evaluate_context_precision(query: str, chunks: list[str], answer: str, *, llm: LLM | None = None) -> float:
    """Rank-weighted share of useful chunks: sum(useful[i] * 1/i) / sum(1/i)."""
    llm = get_llm(llm)
    if not chunks:
        return 0.0
    verdicts = await asyncio.gather(*(
        llm.complete(USEFUL_PROMPT.format(query=query, answer=answer, passage=c)) for c in chunks
    ))
    weights = [1 / i for i in range(1, len(chunks) + 1)]
    useful = [1.0 if is_yes(v) else 0.0 for v in verdicts]
    return clamp01(sum(u * w for u, w in zip(useful, weights)) / sum(weights))
