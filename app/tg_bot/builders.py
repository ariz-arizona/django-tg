# tg_bot/builders.py
import os
import logging
from typing import Optional
from telegram.ext import ApplicationBuilder
from telegram.request import HTTPXRequest

logger = logging.getLogger(__name__)


def create_bot_application(token: str, request: Optional[HTTPXRequest] = None):
    """
    Создать Application
    
    Args:
        token: Токен бота
        request: Опциональный HTTPXRequest (для тестового режима)
    """
    test_mode = os.getenv('TESTING', 'false').lower() == 'true'
    
    if test_mode and request:
        logger.info("🧪 СОЗДАЮ БОТА В ТЕСТОВОМ РЕЖИМЕ С ПЕРЕДАННЫМ REQUEST")
        return (ApplicationBuilder()
                .token(token)
                .request(request)
                .build())
    
    elif test_mode:
        logger.info("🧪 СОЗДАЮ БОТА В ТЕСТОВОМ РЕЖИМЕ (без request)")
        # В тестовом режиме без request - просто создаем без патчинга
        # request будет добавлен позже через app.bot.request = patched_request
        return (ApplicationBuilder()
                .token(token)
                .build())
    
    else:
        logger.info("ℹ️ СОЗДАЮ БОТА В ОБЫЧНОМ РЕЖИМЕ")
        return (ApplicationBuilder()
                .token(token)
                .build())