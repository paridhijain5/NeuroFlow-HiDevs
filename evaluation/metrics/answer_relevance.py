from __future__ import annotations
from ..llm import LLM, Embedder, get_llm, get_embedder
from ._utils import parse_json_array, cosine, clamp01

QUESTIONS_PROMPT = """[task: generate_questions]
Generate 3 to 5 questions that the answer below could be a response to
(the questions an oracle would ask if they received this answer).
Return ONLY a JSON array of strings.
<answer>{answer}</answer>"""


async def evaluate_answer_relevance(query: str, answer: str, *,
                                    llm: LLM | None = None, embedder: Embedder | None = None) -> float:
    """Mean cosine similarity between the original query and questions generated from the answer."""
    llm, embedder = get_llm(llm), get_embedder(embedder)
    raw = await llm.complete(QUESTIONS_PROMPT.format(answer=answer))
    questions = [str(q).strip() for q in parse_json_array(raw) if str(q).strip()][:5]
    if not questions:
        return 0.0
    vectors = await embedder.embed([query] + questions)
    q_vec, gen_vecs = vectors[0], vectors[1:]
    return clamp01(sum(cosine(q_vec, v) for v in gen_vecs) / len(gen_vecs))
