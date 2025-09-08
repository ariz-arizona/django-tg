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


def put_django_task_command_to_bot_queue(bot_id, command):
    # Токен бота из настроек
    bot = Bot.objects.get(id=bot_id, bot_type=Bot.BOT_TYPE_CHOICES[0][0])
    bot_token = bot.token
    if not bot_token:
        return

    queue_key = f"bot_messages_queue_{bot.token}"

    now = int(time.time())
    
    full_command = f"/{command}"
    command_length = len(full_command.split(' ', 1)[0])
    logger.info(f'Отправляем в очередь команду {full_command} с длиной {command_length}')

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
            "text": full_command,
            "entities": [{"type": "bot_command", "offset": 0, "length": command_length}],
        },
    }

    try:
        # Преобразуем в JSON и кладём в Redis
        json_str = json.dumps(update_dict, ensure_ascii=False)
        redis_client.rpush(queue_key, json_str)
    except Exception as e:
        # Можно залогировать, если нужно
        logger.error(f"Ошибка при отправке {command} в очередь: {e}")


@shared_task
def trigger_popular_command(bot_id: int):
    """
    [Админка] Отправляет команду /popular в очередь бота для формирования
    и отправки топ-5 популярных товаров за 24 часа в маркетинговую группу.

    Параметры:
        bot_id (int): ID бота в системе, чей токен будет использован для определения очереди.

    Используется для ручного или автоматического (по расписанию) запуска рассылки.
    """
    put_django_task_command_to_bot_queue(bot_id, "popular")


@shared_task
def trigger_top_brand_command(bot_id: int, exclude_ids=None):
    """
    [Админка] Отправляет команду /top_brand в очередь бота для формирования
    и отправки топ-5 товаров самого активного бренда за 24 часа в маркетинговую группу.

    Параметры:
        bot_id (int): ID бота в системе, чей токен будет использован для определения очереди.
        exclude_ids (list[int], optional): Список ID категорий для исключения.

    Используется для ручного или автоматического (по расписанию) запуска рассылки.
    """
    if exclude_ids is None:
        exclude_ids = []
    # Убедимся, что это список
    if not isinstance(exclude_ids, (list, tuple)):
        exclude_ids = []
    put_django_task_command_to_bot_queue(bot_id, f"top_brand {' '.join(map(str, exclude_ids))}")
    
@shared_task
def trigger_top_category_command(bot_id: int, exclude_ids=None):
    """
    [Админка] Отправляет команду /top_category в очередь бота для формирования
    и отправки топ-5 товаров самой активной категории за 24 часа в маркетинговую группу.

    Параметры:
        bot_id (int): ID бота в системе, чей токен будет использован для определения очереди.
        exclude_ids (list[int], optional): Список ID категорий для исключения.

    Используется для ручного или автоматического (по расписанию) запуска рассылки.
    """
    if exclude_ids is None:
        exclude_ids = []
    # Убедимся, что это список
    if not isinstance(exclude_ids, (list, tuple)):
        exclude_ids = []
    put_django_task_command_to_bot_queue(bot_id, f"top_category {' '.join(map(str, exclude_ids))}")