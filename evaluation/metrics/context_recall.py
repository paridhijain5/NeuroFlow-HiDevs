from __future__ import annotations
import asyncio, re
from ..llm import LLM, get_llm
from ._utils import is_yes, clamp01

ATTRIB_PROMPT = """[task: sentence_attributable]
Can this sentence be attributed to the provided context? Answer with exactly one word: yes or no.
<context>{context}</context>
<sentence>{sentence}</sentence>"""


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


async def evaluate_context_recall(query: str, chunks: list[str], answer: str, *, llm: LLM | None = None) -> float:
    """attributable_sentences / total_sentences."""
    llm = get_llm(llm)
    sentences = split_sentences(answer)
    if not sentences or not chunks:
        return 0.0
    context = "\n\n".join(chunks)
    verdicts = await asyncio.gather(*(
        llm.complete(ATTRIB_PROMPT.format(context=context, sentence=s)) for s in sentences
    ))
    return clamp01(sum(1.0 for v in verdicts if is_yes(v)) / len(sentences))
