"""Offline tests (no API keys, no Redis). Run from backend/:  python tests/test_offline.py"""
import asyncio
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("POSTGRES_PASSWORD", "x")
os.environ.setdefault("REDIS_PASSWORD", "x")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402
import openai  # noqa: E402

from providers.anthropic_provider import AnthropicProvider  # noqa: E402
from providers.base import ChatMessage  # noqa: E402
from providers.client import NeuroFlowClient  # noqa: E402
from providers.fallback import AllProvidersFailed, FallbackChain  # noqa: E402
from providers.openai_provider import OpenAIProvider  # noqa: E402
from providers.router import ModelConfig, ModelRouter, RoutingCriteria  # noqa: E402


class FakeRedis:
    def __init__(self):
        self.d = {}

    async def get(self, k):
        return self.d.get(k)

    async def set(self, k, v, nx=False):
        if nx and k in self.d:
            return None
        self.d[k] = v
        return True

    async def incr(self, k):
        self.d[k] = int(self.d.get(k, 0)) + 1

    async def incrbyfloat(self, k, v):
        self.d[k] = float(self.d.get(k, 0)) + v


def rate_limit_error():
    req = httpx.Request("POST", "http://x")
    resp = httpx.Response(429, headers={"retry-after": "0"}, request=req)
    return openai.RateLimitError("rate limited", response=resp, body=None)


def ok_response():
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="hi"), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=500),
    )


class FlakyCompletions:
    def __init__(self, failures):
        self.failures, self.calls = failures, 0

    async def create(self, **kw):
        self.calls += 1
        if self.calls <= self.failures:
            raise rate_limit_error()
        if kw.get("stream"):
            async def gen():
                for t in ["He", "llo"]:
                    yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=t), finish_reason=None)], usage=None)
                yield SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=10, completion_tokens=2))
            return gen()
        return ok_response()


def fake_openai(failures=0):
    comp = FlakyCompletions(failures)
    client = SimpleNamespace(chat=SimpleNamespace(completions=comp))
    p = OpenAIProvider(model="gpt-4o-mini", client=client)
    delays = []

    async def fake_sleep(s):
        delays.append(s)

    p._sleep = fake_sleep
    return p, comp, delays


MSG = [ChatMessage("user", "hi")]


async def test_retry_succeeds_after_429s():
    p, comp, delays = fake_openai(failures=2)
    r = await p.complete(MSG)
    assert r.content == "hi" and comp.calls == 3 and delays == [1.0, 2.0], (comp.calls, delays)
    # cost: 1000 in * 0.15/M + 500 out * 0.60/M
    assert abs(r.cost_usd - (1000 * 0.15 + 500 * 0.60) / 1e6) < 1e-12


async def test_retry_gives_up_after_3_retries():
    p, comp, _ = fake_openai(failures=99)
    try:
        await p.complete(MSG)
        raise AssertionError("should raise")
    except openai.RateLimitError:
        pass
    assert comp.calls == 4  # 1 try + 3 retries


async def test_streaming_progressive_and_usage():
    p, _, _ = fake_openai()
    tokens = [t async for t in p.stream(MSG)]
    assert tokens == ["He", "llo"]
    events = [e async for e in p.stream_events(MSG)]
    assert events[-1].content == "Hello" and events[-1].input_tokens == 10


async def test_embed_batches_of_100():
    calls = []

    async def create(model, input):
        calls.append(len(input))
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=[0.1]) for i in range(len(input))])

    client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    p = OpenAIProvider(client=client)
    out = await p.embed(["t"] * 250)
    assert calls == [100, 100, 50] and len(out) == 250


def test_anthropic_system_mapping():
    system, msgs = AnthropicProvider._split([
        ChatMessage("system", "be brief"), ChatMessage("user", "q"),
        ChatMessage("user", [{"type": "text", "text": "see"},
                             {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]),
    ])
    assert system == "be brief" and all(m["role"] != "system" for m in msgs)
    assert msgs[1]["content"][1]["source"] == {"type": "base64", "media_type": "image/png", "data": "AAAA"}


async def test_router_rules():
    router = ModelRouter(FakeRedis())
    assert (await router.route(RoutingCriteria())).name == "gpt-4o-mini"  # cheapest default
    assert (await router.route(RoutingCriteria(require_vision=True))).supports_vision
    long = await router.route(RoutingCriteria(require_long_context=True))
    assert long.context_window > 100_000
    judge = await router.route(RoutingCriteria(task_type="evaluation"))
    assert judge.capable and not judge.is_fine_tuned
    emb = await router.route(RoutingCriteria(task_type="embedding"))
    assert emb.kind == "embedding"
    # cost cap filters expensive models
    cheap = await router.route(RoutingCriteria(max_cost_per_call=0.001))
    assert cheap.estimated_cost(1000, 500) <= 0.001

    await router.register_model(ModelConfig("ft:gpt-4o-mini:acme", "openai", supports_vision=True,
                                            cost_input_per_m=0.3, cost_output_per_m=1.2,
                                            is_fine_tuned=True, fine_tuned_for="rag_generation", capable=True))
    assert (await router.route(RoutingCriteria(prefer_fine_tuned=True))).is_fine_tuned
    assert not (await router.route(RoutingCriteria())).is_fine_tuned  # not preferred -> base
    # evaluation never uses a fine-tuned model, even if preferred
    ev = await router.route(RoutingCriteria(task_type="evaluation", prefer_fine_tuned=True))
    assert not ev.is_fine_tuned


async def test_client_tracks_costs_in_redis():
    redis = FakeRedis()
    p, _, _ = fake_openai()
    client = NeuroFlowClient(redis, providers={"gpt-4o-mini": p})
    await client.chat(MSG)
    await client.chat(MSG)
    assert redis.d["metrics:model:gpt-4o-mini:calls"] == 2
    assert redis.d["metrics:model:gpt-4o-mini:cost_usd"] > 0


async def test_fallback_chain():
    bad = SimpleNamespace(model="bad")

    async def boom(*a, **k):
        raise RuntimeError("401 invalid key")

    bad.complete = boom
    good, _, _ = fake_openai()
    r = await FallbackChain([bad, good]).complete(MSG)
    assert r.model == "gpt-4o-mini"
    try:
        await FallbackChain([bad]).complete(MSG)
        raise AssertionError("should raise")
    except AllProvidersFailed:
        pass


async def main():
    test_anthropic_system_mapping()
    for fn in [test_retry_succeeds_after_429s, test_retry_gives_up_after_3_retries,
               test_streaming_progressive_and_usage, test_embed_batches_of_100,
               test_router_rules, test_client_tracks_costs_in_redis, test_fallback_chain]:
        await fn()
        print("PASS", fn.__name__)
    print("PASS test_anthropic_system_mapping\nAll offline tests passed")


asyncio.run(main())
