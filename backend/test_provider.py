"""Live smoke test (needs real API keys in ../.env). Run from backend/:  python test_provider.py"""
import asyncio

from config import settings
from providers.anthropic_provider import AnthropicProvider
from providers.base import ChatMessage
from providers.openai_provider import OpenAIProvider


async def stream_demo(provider):
    print(f"\n[{provider.name}:{provider.model}] stream: ", end="", flush=True)
    async for token in provider.stream([ChatMessage(role="user", content="Say one word")]):
        print(token, end="", flush=True)
    print()


async def main():
    openai_key = settings.openai_api_key or settings.llm_api_key
    if openai_key:
        oa = OpenAIProvider(api_key=openai_key, base_url=settings.openai_base_url)
        vectors = await oa.embed(["hello world"])
        print(f"[openai] embed: {len(vectors)} vector(s), dim={len(vectors[0])}, head={vectors[0][:4]}")
        await stream_demo(oa)
    else:
        print("No OPENAI_API_KEY set; skipping OpenAI")
    if settings.anthropic_api_key:
        await stream_demo(AnthropicProvider(api_key=settings.anthropic_api_key))
    else:
        print("No ANTHROPIC_API_KEY set; skipping Anthropic")


asyncio.run(main())
