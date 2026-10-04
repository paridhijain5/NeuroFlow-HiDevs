from __future__ import annotations
import asyncio
from ..llm import LLM, get_llm
from ._utils import parse_json_array, verdict_score, clamp01

CLAIMS_PROMPT = """[task: extract_claims]
List every factual statement made in the answer below as a JSON array of strings.
Return ONLY the JSON array.
<answer>{answer}</answer>"""

VERIFY_PROMPT = """[task: verify_claim]
Is this claim supported by the context? Answer with exactly one word: yes, no, or partial.
<context>{context}</context>
<claim>{claim}</claim>"""


async def evaluate_faithfulness(query: str, answer: str, context: str, *, llm: LLM | None = None) -> float:
    """Are all claims in the answer grounded in the retrieved context? (supported / total, partial = 0.5)"""
    llm = get_llm(llm)
    raw = await llm.complete(CLAIMS_PROMPT.format(answer=answer))
    claims = [str(c).strip() for c in parse_json_array(raw) if str(c).strip()]
    if not claims:
        return 1.0                      # nothing asserted -> nothing ungrounded
    if not context.strip():
        return 0.0                      # claims made but context is empty
    verdicts = await asyncio.gather(*(
        llm.complete(VERIFY_PROMPT.format(context=context, claim=c)) for c in claims
    ))
    return clamp01(sum(verdict_score(v) for v in verdicts) / len(claims))
