import os
import redis
import asyncio
import json
from django.urls import reverse
from django.conf import settings
import signal

from celery import shared_task

from telegram.ext import ApplicationBuilder, CommandHandler
from telegram import Update

from tg_bot.bot.parser import ParserBot
from tg_bot.bot.tarot import TarotBot

from server.logger import logger


# Настроим соединение с Redis
redis_client = redis.StrictRedis(
    host=os.getenv("REDIS_HOST"), port=os.getenv("REDIS_PORT"), db=2
)


# Асинхронная обработка бота
async def run_bot(token, handlersClass):
    # Динамически создаем класс по имени
    bot_class = globals().get(handlersClass)
    if not bot_class:
        logger.error(f"Класс {handlersClass} не найден")
        return

    bot_instance = bot_class()  # Создаем экземпляр класса
    handlers = bot_instance.handlers  # Получаем хэндлеры

    app = ApplicationBuilder().token(token).build()
    await app.initialize()

    for handler in handlers:
        app.add_handler(handler)

    webhook_url = reverse(viewname="webhook", kwargs={"token": token})
    webhook_url = "".join([settings.TG_WEBHOOK_HOST, webhook_url])
    logger.info(f"Попытка установить вебхук {webhook_url}")
    await app.bot.set_webhook(
        webhook_url, drop_pending_updates=True
    )  # Асинхронная установка webhook

    while True:
        try:
            # Извлекаем сообщение из очереди
            message = redis_client.lpop("bot_messages_queue")
            if message:
                try:
                    # Декодируем сообщение
                    json_str = message.decode("utf-8")
                    logger.info(f"Сообщение из очереди: {json_str[0:50]}")

                    # Преобразуем в объект Update
                    data = json.loads(json_str)
                    update = Update.de_json(data, app.bot)
                    await app.process_update(update)
                    # await app.update_queue.put(update)

                except Exception as e:
                    logger.error(f"Ошибка при обработке сообщения: {e}", exc_info=True)

            # Пауза между попытками извлечь следующее сообщение
            await asyncio.sleep(0.3)

        except Exception as e:
            logger.error(
                f"Ошибка при обработке бота с токеном {token}: {e}", exc_info=True
            )


@shared_task(bind=True)
def process_bot(self, token, handlersClass):
    lock_key = f"bot_processing_lock_{token}"

    def cleanup_on_exit(signum, frame):
        logger.info("Завершаем работу контейнера и очищаем ресурсы...")
        # Выполним нужные очистки, например, удалим блокировку Redis
        redis_client.delete(lock_key)

    # Попробуем установить блокировку, если она уже установлена — выходим
    if not redis_client.setnx(lock_key, "locked"):
        logger.info(f"Задача для бота с токеном {token} уже выполняется. Пропускаем.")
        return

    signal.signal(signal.SIGTERM, cleanup_on_exit)

    try:
        logger.info(f"Начало обработки бота с токеном: {token}")

        # Получаем текущий цикл событий
        loop = asyncio.get_event_loop()

        # Запускаем асинхронный процесс бота внутри текущего цикла событий
        loop.run_until_complete(
            run_bot(token, handlersClass)
        )  # Запускаем асинхронную задачу

    except Exception as e:
        logger.error(f"Ошибка при обработке бота с токеном {token}: {e}", exc_info=True)
    finally:
        redis_client.delete(lock_key)  # Удаляем блокировку
        logger.info(f"Завершение обработки бота с токеном {token}, блокировка удалена.")
