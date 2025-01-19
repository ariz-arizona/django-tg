# tg_bot/apps.py
import os
import sys
from django.apps import AppConfig
from django.db import connection
from django.core.management import call_command
from server.logger import logger

class TgBotConfig(AppConfig):
    name = 'tg_bot'
    _task_started = False

    def ready(self):
        if self._task_started:
            logger.info("Задача уже была запущена. Пропускаем.")
            return
        # Если запущен runserver, проверяем, что это основной процесс
        if 'runserver' in sys.argv:
            # Проверяем RUN_MAIN для основного процесса
            if os.environ.get('RUN_MAIN', None) == 'true':
                logger.info("Инициализация приложения TgBot (основной процесс runserver).")
                self.start_bot_processing()
            else:
                logger.info("Пропуск инициализации, так как это дочерний процесс runserver.")
        else:
            if 'celery' not in sys.argv:
                logger.info("Инициализация приложения TgBot (не runserver).")
                self.start_bot_processing()

    def start_bot_processing(self):
        try:
            logger.info("Запуск обработки ботов...")
            call_command('start_bot_processing')
            self._task_started = True 

        except Exception as e:
            logger.error(f"Ошибка при запуске задачи обработки ботов: {e}")
