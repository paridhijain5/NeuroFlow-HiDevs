import asyncpg
import httpx
import redis.asyncio as aioredis


async def check_postgres(pool: asyncpg.Pool) -> bool:
    try:
        async with pool.acquire() as conn:
            return await conn.fetchval("SELECT 1") == 1
    except Exception:
        return False


async def check_redis(client: aioredis.Redis) -> bool:
    try:
        return bool(await client.ping())
    except Exception:
        return False


async def check_mlflow(tracking_uri: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get(f"{tracking_uri.rstrip('/')}/health")
            return resp.status_code == 200
    except Exception:
        return False
