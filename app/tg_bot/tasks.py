import os
import redis.asyncio as redis
import asyncio
import json
from datetime import datetime

from django.urls import reverse
from django.conf import settings
import signal

from celery import shared_task

from telegram.ext import ApplicationBuilder, CommandHandler
from telegram import Update

from tg_bot.models import Bot
from cardparser.bot.parser import ParserBot
from tarot.bot.tarot import TarotBot
from roster.bot.roster import GachaBot

from server.logger import logger


# Настроим соединение с Redis
redis_client = redis.StrictRedis(
    host=os.getenv("REDIS_HOST"), 
    port=os.getenv("REDIS_PORT"), 
    db=2,
    decode_responses=True
)


# Асинхронная обработка бота
async def run_bot(token, app_bot_id, handlersClass):
    bot_class = globals().get(handlersClass)
    if not bot_class:
        logger.error(f"Класс {handlersClass} не найден")
        return

    bot_instance = bot_class()
    handlers = bot_instance.handlers

    # === ТОЛЬКО ЭТО ===
    test_mode = os.getenv('TESTING', 'false').lower() == 'true'
    
    if test_mode:
        from tests.conftest import RedisLoggingHTTPXRequest
        
        test_redis = redis.StrictRedis(
            host=os.getenv("REDIS_HOST", "localhost"), 
            port=int(os.getenv("REDIS_PORT", 6379)), 
            db=3,
            decode_responses=True
        )
        
        patched_request = RedisLoggingHTTPXRequest(redis_client=test_redis)
        
        app = (ApplicationBuilder()
               .token(token)
               .request(patched_request)
               .build())
    else:
        app = ApplicationBuilder().token(token).build()
    # === КОНЕЦ ===
    
    await app.initialize()
    
    bot_instance.app_bot_id = app_bot_id
    logger.info(f"Инициализирован бот с ID: {bot_instance.app_bot_id}")

    for handler in handlers:
        app.add_handler(handler)

    webhook_url = reverse(viewname="webhook", kwargs={"token": token})
    webhook_url = "".join([settings.TG_WEBHOOK_HOST, webhook_url])
    logger.info(f"Попытка установить вебхук {webhook_url}")
    await app.bot.set_webhook(
        webhook_url, drop_pending_updates=True
    )
    
    try:
        bot_model = await Bot.objects.aget(id=app_bot_id)
        bot_username = app.bot.username
        
        if bot_model.username != bot_username:
            await Bot.objects.filter(id=app_bot_id).aupdate(username=bot_username)
            logger.info(f"Обновлен username для бота {app_bot_id}: @{bot_username}")
        else:
            logger.info(f"Username для бота {app_bot_id} уже актуален: @{bot_username}")
            
    except Bot.DoesNotExist:
        logger.error(f"Бот с ID {app_bot_id} не найден в базе данных")
        bot_username = None
    except Exception as e:
        logger.error(f"Ошибка при обновлении username бота: {e}")
        bot_username = None
    
    bot_info = {
        'bot_id': app_bot_id,
        'username': app.bot.username,
        'type': handlersClass,
        'started_at': datetime.now().isoformat(),
    }
    await redis_client.hset("running_bots", app_bot_id, json.dumps(bot_info))
    logger.info(f"Бот {app_bot_id} зарегистрирован в Redis")

    pubsub = redis_client.pubsub()
    channel_name = f"bot_messages_queue_{token}"
    await pubsub.subscribe(channel_name)
    
    logger.info(f"Ожидание сообщений через Pub/Sub в канале: {channel_name}")

    try:
        async for message in pubsub.listen():
            if message['type'] != 'message':
                continue

            try:
                data = json.loads(message['data'])
                update = Update.de_json(data, app.bot)
                await app.process_update(update)

            except Exception as e:
                logger.error(f"Ошибка при обработке сообщения: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"Ошибка в подписке бота {token}: {e}", exc_info=True)
    finally:
        await pubsub.unsubscribe(channel_name)
        await pubsub.close()


@shared_task(bind=True)
def process_bot(self, token, handlersClass):
    lock_key = f"bot_processing_lock_{token}"

    def cleanup_on_exit(signum, frame):
        logger.info("Завершаем работу контейнера и очищаем ресурсы...")
        redis_client.delete(lock_key)

    if not redis_client.setnx(lock_key, "locked"):
        logger.info(f"Задача для бота с токеном {token} уже выполняется. Пропускаем.")
        return

    signal.signal(signal.SIGTERM, cleanup_on_exit)

    try:
        logger.info(f"Начало обработки бота с токеном: {token}")
        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            run_bot(token, handlersClass)
        )
    except Exception as e:
        logger.error(f"Ошибка при обработке бота с токеном {token}: {e}", exc_info=True)
    finally:
        redis_client.delete(lock_key)
        logger.info(f"Завершение обработки бота с токеном {token}, блокировка удалена.")