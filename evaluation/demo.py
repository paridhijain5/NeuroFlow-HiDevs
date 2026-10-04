"""Offline demo: python -m evaluation.demo   (no DB / Redis / API keys needed)"""
import asyncio, logging, time

from .judge import EvaluationJudge
from .mock_llm import MockLLM, MockEmbedder

logging.basicConfig(level=logging.INFO, format="%(message)s")


class FakeDB:
    async def execute(self, sql, *args):
        print(f"[db] {' '.join(sql.split())[:70]}...")


async def main():
    llm = MockLLM(delay=0.3)               # each LLM call "takes" 300 ms
    judge = EvaluationJudge(FakeDB(), llm, MockEmbedder())
    chunks = ["The Eiffel Tower was completed in 1889 in Paris. It stands about 330 metres tall.",
              "Penguins migrate across Antarctic glaciers every winter."]
    answer = "The Eiffel Tower was completed in 1889 in Paris. It stands about 330 metres tall."
    t = time.perf_counter()
    res = await judge.evaluate("run-123", "How tall is the Eiffel Tower and when was it built?", answer, chunks)
    wall = time.perf_counter() - t
    print(res.to_dict())
    print(f"LLM calls: {llm.calls}, wall time: {wall:.2f}s (sequential would be ~{llm.calls * 0.3:.1f}s)")


asyncio.run(main())
