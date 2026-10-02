"""Placeholder background worker (real job handling arrives in Task 4)."""
import asyncio
import logging

import redis.asyncio as aioredis

from config import settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("worker")


async def main() -> None:
    client = aioredis.Redis(
        host=settings.redis_host, port=settings.redis_port, password=settings.redis_password
    )
    await client.ping()
    log.info("Worker connected to Redis; waiting for jobs")
    while True:
        await asyncio.sleep(30)


if __name__ == "__main__":
    asyncio.run(main())
