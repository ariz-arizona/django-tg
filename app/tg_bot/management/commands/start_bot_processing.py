import asyncio
import os
from asgiref.sync import sync_to_async

from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import models
from django.utils.autoreload import run_with_reloader

from server.logger import logger

class Command(BaseCommand):
    help = "Запуск процесса для всех ботов"

    def handle(self, *args, **kwargs):
        # Запускаем с авто-перезагрузкой
        run_with_reloader(self.run_bots)

    def run_bots(self):
        from tg_bot.models import Bot
        from tg_bot.tasks import run_bot

        logger.info("Запуск обработки ботов напрямую...")
        
        instance_name = os.getenv("INSTANCE_NAME")

        query = Bot.objects.filter(is_enabled=True)
        if instance_name:
            query = query.filter(models.Q(docker_instance_name=instance_name) | models.Q(docker_instance_name__isnull=True))
        
        bots = list(query)

        async def main():
            if not settings.TG_WEBHOOK_HOST_RAW:
                logger.error("Переменная TG_WEBHOOK_HOST_RAW не задана.")
                await asyncio.Future()
                return

            if not bots:
                logger.warning("Боты не найдены в базе данных.")
                await asyncio.Future()
                return

            tasks = []
            for bot in bots:
                logger.info(f"Подготовка задачи для бота: {bot.name} (ID {bot.id})")
                tasks.append(run_bot(bot.token, bot.id, bot.bot_type, ))
            
            if tasks:
                await asyncio.gather(*tasks)
            else:
                logger.warning("Боты не найдены в базе данных.")

        try:
            asyncio.run(main())
        except KeyboardInterrupt:
            logger.info("Процесс остановлен пользователем.")
        except Exception as e:
            logger.error(f"Критическая ошибка воркера: {e}", exc_info=True)