import asyncio
import os
from asgiref.sync import sync_to_async

from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import models

from server.logger import logger

class Command(BaseCommand):
    help = "Запуск процесса для всех ботов"

    def handle(self, *args, **kwargs):
        from tg_bot.models import Bot
        from tg_bot.tasks import run_bot # Тот самый асинхронный метод с while True

        logger.info("Запуск обработки ботов напрямую...")
        
        instance_name = os.getenv("INSTANCE_NAME")

        # Ищем ботов, назначенных на этот инстанс, 
        # ИЛИ тех, у кого инстанс вообще не задан (если хотите иметь "дефолтный" воркер)
        query = Bot.objects.filter(is_enabled=True)
        if instance_name:
            query = query.filter(models.Q(docker_instance_name=instance_name) | models.Q(docker_instance_name__isnull=True))
        
        bots = list(query)

        async def main():
            # 1. Проверка настроек
            if not settings.TG_WEBHOOK_HOST_RAW:
                logger.error("Переменная TG_WEBHOOK_HOST_RAW не задана.")
                await asyncio.Future()
                return

            # 2. Получаем список ботов и сразу проверяем их наличие
            if not bots:
                logger.warning("Боты не найдены в базе данных.")
                await asyncio.Future()
                return

            tasks = []
            for bot in bots:
                logger.info(f"Подготовка задачи для бота: {bot.name} (ID {bot.id})")
                # 2. Создаем список задач
                tasks.append(run_bot(bot.token, bot.id, bot.bot_type, ))
            
            # 3. Запускаем всё параллельно и ждем вечно
            if tasks:
                await asyncio.gather(*tasks)
            else:
                logger.warning("Боты не найдены в базе данных.")

        # Запускаем асинхронный цикл
        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            logger.info("Процесс остановлен пользователем.")
        except Exception as e:
            logger.error(f"Критическая ошибка воркера: {e}", exc_info=True)
