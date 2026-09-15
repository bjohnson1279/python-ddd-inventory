import json
from typing import Any, Optional
import redis.asyncio as redis
import os

REDIS_URL = os.getenv('REDIS_URL')
if not REDIS_URL:
    raise ValueError("REDIS_URL environment variable is not set")
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

class DistributedCache:
    """Tier-2 Distributed Redis Cache."""

    @staticmethod
    async def get(key: str) -> Optional[Any]:
        val = await redis_client.get(key)
        if val:
            return json.loads(val)
        return None

    @staticmethod
    async def set(key: str, value: Any, ttl: int = 3600):
        await redis_client.setex(key, ttl, json.dumps(value))

    @staticmethod
    async def invalidate(key: str):
        await redis_client.delete(key)

    @staticmethod
    async def invalidate_pattern(pattern: str):
        keys = await redis_client.keys(pattern)
        if keys:
            await redis_client.delete(*keys)
