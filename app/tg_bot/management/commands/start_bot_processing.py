import asyncio
from django.core.management.base import BaseCommand
from asgiref.sync import sync_to_async
from server.logger import logger
from django.conf import settings

class Command(BaseCommand):
    help = "Запуск процесса для всех ботов"

    def handle(self, *args, **kwargs):
        from tg_bot.models import Bot
        from tg_bot.tasks import run_bot # Тот самый асинхронный метод с while True

        logger.info("Запуск обработки ботов напрямую...")

        # 1. Получаем список ботов СИНХРОННО (до запуска asyncio.run)
        # Делаем list(), чтобы запрос в базу выполнился прямо здесь
        bots = list(Bot.objects.filter(is_enabled=True))

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
                tasks.append(run_bot(bot.token, bot.bot_type))
            
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
