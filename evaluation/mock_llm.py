"""Deterministic offline stand-ins (heuristics, NOT a real judge) for demos and tests."""
from __future__ import annotations
import asyncio, json, re, zlib

STOP = set("the a an of in on and to is are was were it its that this for with as by at from be or".split())


def words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP}


def overlap(a: str, b: str) -> float:
    wa = words(a)
    return len(wa & words(b)) / len(wa) if wa else 0.0


def tag(prompt: str, name: str) -> str:
    m = re.search(rf"<{name}>(.*?)</{name}>", prompt, re.S)
    return m.group(1).strip() if m else ""


class MockLLM:
    def __init__(self, delay: float = 0.0):
        self.delay, self.calls = delay, 0

    async def complete(self, prompt: str, *, temperature: float = 0.0) -> str:
        self.calls += 1
        await asyncio.sleep(self.delay)
        task = re.match(r"\[task: (\w+)\]", prompt).group(1)
        if task == "extract_claims":
            return json.dumps([s for s in re.split(r"(?<=[.!?])\s+", tag(prompt, "answer")) if s])
        if task == "verify_claim":
            ov = overlap(tag(prompt, "claim"), tag(prompt, "context"))
            return "yes" if ov >= 0.6 else "partial" if ov >= 0.3 else "no"
        if task == "generate_questions":
            sents = [s for s in re.split(r"(?<=[.!?])\s+", tag(prompt, "answer")) if s][:4]
            return json.dumps([f"What about: {s.rstrip('.!?')}?" for s in sents] or ["What is this?"])
        if task == "passage_useful":
            return "yes" if overlap(tag(prompt, "answer"), tag(prompt, "passage")) >= 0.15 else "no"
        if task == "sentence_attributable":
            return "yes" if overlap(tag(prompt, "sentence"), tag(prompt, "context")) >= 0.5 else "no"
        return "no"


class MockEmbedder:
    DIM = 256

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * self.DIM
            for w in words(t):
                v[zlib.crc32(w.encode()) % self.DIM] += 1.0
            out.append(v)
        return out
