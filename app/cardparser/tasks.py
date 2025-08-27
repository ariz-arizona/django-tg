# cardparser/tasks.py
import time
import json
import redis
import os
from celery import shared_task
from django.conf import settings

from server.logger import logger
from tg_bot.models.base import Bot


redis_client = redis.StrictRedis(
    host=os.getenv("REDIS_HOST"), port=os.getenv("REDIS_PORT"), db=2
)


@shared_task
def trigger_popular_command(bot_id: int):
    """
    Задача: имитирует команду /popular от администратора.
    Кладёт update в очередь — бот обрабатывает как обычное сообщение.
    """
    # Токен бота из настроек
    bot = Bot.objects.get(id=bot_id, bot_type=Bot.BOT_TYPE_CHOICES[0][0])
    bot_token = bot.token
    if not bot_token:
        return

    queue_key = f"bot_messages_queue_{bot.token}"

    now = int(time.time())
    
    # Пример update — как если бы админ написал /popular в личку
    update_dict = {
        "update_id": now,
        "message": {
            "message_id": now % 100000,
            "from": {
                "id": bot.chat_id,
                "is_bot": False,
                "first_name": "django_task",
            },
            "chat": {
                "id": bot.chat_id,  
                "type": "private",
            },
            "date": now, 
            "text": "/popular",
            "entities": [{"type": "bot_command", "offset": 0, "length": 9}],
        },
    }

    try:
        # Преобразуем в JSON и кладём в Redis
        json_str = json.dumps(update_dict, ensure_ascii=False)
        redis_client.rpush(queue_key, json_str)
    except Exception as e:
        # Можно залогировать, если нужно
        logger.error(f"Ошибка при отправке /popular в очередь: {e}")
