# tg_bot/management/commands/start_bot_processing.py
from django.core.management.base import BaseCommand
from server.logger import logger


class Command(BaseCommand):
    help = "Запуск процесса для всех ботов"

    def handle(self, *args, **kwargs):
        from tg_bot.models import Bot
        from tg_bot.tasks import process_bot

        logger.info("Запуск команды start_bot_processing.")

        try:
            logger.info("Запуск обработки ботов...")
            from tg_bot.models import Bot

            # Получаем всех ботов
            bots = Bot.objects.all()
            for bot in bots:
                logger.info(f"Запуск задачи для бота с ID {bot.id}...")
                process_bot.delay(bot.token, "ParserBot")

        except Exception as e:
            logger.error(f"Ошибка при запуске задачи обработки ботов: {e}")
