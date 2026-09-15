import os
import redis.asyncio as aioredis

# --- Конфигурация ---
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_TTL_SECONDS = 10
REDIS_KEY_TEMPLATE = "user:{user_id}:{category}:{app_id}"


def get_redis_client(db: int = 0, decode_responses: bool = True) -> aioredis.StrictRedis:
    """Фабрика клиентов Redis."""
    return aioredis.StrictRedis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=db,
        decode_responses=decode_responses,
    )


# --- Готовые клиенты для импорта ---
redis_client = get_redis_client(db=3)      # для кулдаунов гаданий
redis_client_bot = get_redis_client(db=2)  # для данных ботов