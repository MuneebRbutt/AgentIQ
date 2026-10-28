"""Redis client factory. Redis is a cache and job queue only; Postgres is the source of truth."""

from redis.asyncio import Redis

from app.core.config import Settings


def create_redis(settings: Settings) -> Redis:
    return Redis.from_url(settings.redis_url.get_secret_value(), decode_responses=True)


async def ping_redis(client: Redis) -> None:
    """Raise if Redis doesn't answer PING."""
    await client.ping()
