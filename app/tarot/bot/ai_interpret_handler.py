# ai_interpret_handler.py
import re
import os
from typing import List, Optional, Dict

import time
import json
import asyncio
import redis.asyncio as aioredis
import aiohttp
import random
from io import BytesIO
from bs4 import BeautifulSoup
from openai import AsyncOpenAI

from telegram import Update, InputMediaPhoto, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Message
from telegram.ext import (
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    CallbackContext,
    filters,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter

from django.utils.timezone import now, timedelta
from django.core.exceptions import ObjectDoesNotExist
from django.db.models.functions import Cast
from django.db.models import IntegerField

from tg_bot.bot.abstract import AbstractBot
from tg_bot.models import (
    TgUser, Bot
)
from tarot.models import (
    TarotDeck,
    TarotCardItem,
    TarotCard,
    ExtendedMeaning,
    OraculumDeck,
    OraculumItem,
    Rune,
    UserReading,
    AIReadingInterpretation,
    AIApiKey
)
from tg_bot.models import BotFileCache
from server.logger import logger
from django.conf import settings

from tarot.utils.image_utils import create_spread_image
from tarot.bot.allcard_handler import AllCardHandler

# Инициализируем асинхронный клиент
redis_client = aioredis.StrictRedis(
    host=os.getenv("REDIS_HOST", "localhost"), 
    port=int(os.getenv("REDIS_PORT", 6379)), 
    db=3,
    decode_responses=True # Рекомендуется: автоматически декодирует bytes в строки python
)
redis_client_bot = aioredis.StrictRedis(
    host=os.getenv("REDIS_HOST", "localhost"), 
    port=int(os.getenv("REDIS_PORT", 6379)), 
    db=2,
    decode_responses=True # Рекомендуется: автоматически декодирует bytes в строки python
)

REDIS_TTL_SECONDS = 10
REDIS_KEY_TEMPLATE = "user:{user_id}:{category}"

class AIInterpretHandler:
    """Обработчик для запуска ИИ-интерпретации раскладов."""

    def __init__(self, bot_instance):
        """
        Инициализация обработчика.

        Args:
            bot_instance: Экземпляр основного бота для доступа к его методам и логам
        """
        self.bot = bot_instance
    
    @property
    def app_bot_id(self):
        """Получаем app_bot_id из основного бота для фильтрации ключей/логов."""
        return self.bot.app_bot_id

    def get_handlers(self):
        """Возвращает список обработчиков для ИИ-функционала."""
        return [
            # Ловим только callback-кнопку, так как текстовой команды на запуск ИИ обычно нет
            CallbackQueryHandler(
                self.handle_ai_reading_callback,
                pattern=r"^aireading_",
            ),
        ]

    async def should_add_ai_button(self) -> bool:
        """
        Проверяет кулдаун на ИИ-генерации.
        Если передан bot_obj, ищет его активный ключ и фильтрует интерпретации 
        по провайдеру и идентификатору проекта этого ключа.
        Иначе — проверяет глобально по всей базе.
        """
        AI_TIMEOUT = timedelta(seconds=60)
        
        try:
            # Базовый кверисет для поиска прошлых интерпретаций
            interpretations_qs = AIReadingInterpretation.objects.all()
            bot_obj = await Bot.objects.filter(id=self.app_bot_id).afirst()
            
            if bot_obj:
                # 1. Ищем активный и неисчерпанный ключ для данного бота
                # Берем самый свежий из доступных (или добавь свою логику приоритета)
                active_key = await AIApiKey.objects.filter(
                    bot=bot_obj,
                    is_active=True,
                    is_exhausted=False
                ).order_by("-updated_at").afirst()
                
                if active_key:
                    # 2. Фильтруем прошлые запуски ИИ именно по этому провайдеру и проекту.
                    # Учитываем, что проект может быть пустой строкой или None.
                    interpretations_qs = interpretations_qs.filter(
                        ai_key__provider=active_key.provider,
                        ai_key__project_identifier=active_key.project_identifier
                    )
                    logger.info(
                        f"Проверка кулдауна ИИ для бота {bot_obj.username} по ключу: "
                        f"Провайдер={active_key.provider}, Проект={active_key.project_identifier or 'дефолт'}"
                    )
                else:
                    # Если у бота вообще нет активного ключа в базе, то и кулдаун проверять не для кого.
                    # Кнопку можно не добавлять (или вернуть True, а ошибку отловить уже при клике)
                    logger.warning(f"У бота {bot_obj.username} не найдено активных AI-ключей.")
                    return False
            else:
                logger.info("Проверка кулдауна ИИ выполняется глобально (без привязки к боту/ключу).")

            # 3. Получаем самую последнюю интерпретацию по выстроенным фильтрам
            latest_interpretation = await interpretations_qs.order_by("-created_at").afirst()
            
            if latest_interpretation is None:
                # Запросов с такими параметрами еще не было — кулдауна нет
                return True
                
            time_passed = now() - latest_interpretation.created_at
            
            if time_passed > AI_TIMEOUT:
                return True
                
            logger.info(
                f"ИИ-лимит активен. На этом провайдере/проекте запрос был {time_passed.total_seconds():.1f} сек. назад. "
                f"Кнопка скрыта."
            )
            return False
            
        except Exception as e:
            logger.error(f"Ошибка при проверке кулдауна ИИ: {e}", exc_info=True)
            return False

    async def run_ai_interpretation(self, reading: UserReading, message: Message) -> str:
        """
        Выполняет запрос к AI на основе сохраненного расклада.
        Стримит ответ в Telegram, редактируя переданное сообщение `message`.
        """
        # 1. Получаем бота и его активный ключ
        bot_obj = await Bot.objects.filter(id=self.app_bot_id).afirst()
        if not bot_obj:
            raise ValueError(f"Бот с ID {self.app_bot_id} не найден в БД.")

        active_key = await AIApiKey.objects.filter(
            bot=bot_obj,
            is_active=True,
            is_exhausted=False
        ).order_by("-updated_at").afirst()

        if not active_key:
            raise ValueError(f"У бота {bot_obj.username} нет активных API-ключей.")

        # 2. Определяем параметры подключения и модель
        base_url = active_key.custom_base_url if active_key.provider == AIApiKey.ProviderUrl.CUSTOM else active_key.provider
        model_name = active_key.override_model_name or "gemini-2.0-flash"

        prompt_system = active_key.system_prompt
        prompt_user = (
            f"Сделай мне трактовку расклада.\n"
            f"Инструмент/Категория: {reading.get_category_display()}\n"
            f"Выпавшие карты/руны: {reading.text}"
        )
        if reading.original_query:
            prompt_user += f"\n\n💬 Вопрос/Контекст пользователя: {reading.original_query.strip()}"
        
        # 3. Создаем предварительную запись в базе со статусом PENDING
        ai_log = await AIReadingInterpretation.objects.acreate(
            reading=reading,
            ai_key=active_key,
            model_used=model_name,
            prompt_system=prompt_system,
            prompt_user=prompt_user,
            status=AIReadingInterpretation.AIStatus.PENDING
        )

        logger.info(f"Запущен стриминг-запрос к ИИ [Лог #{ai_log.id}] через {base_url}, модель: {model_name}")

        try:
            # 4. Инициализируем клиент OpenAI
            client = AsyncOpenAI(
                api_key=active_key.api_key,
                base_url=base_url
            )

            extra_headers = {}
            if active_key.project_identifier:
                extra_headers["X-Goog-User-Project"] = active_key.project_identifier

            # 5. Делаем асинхронный запрос с параметром stream=True
            response_stream = await client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": prompt_system},
                    {"role": "user", "content": prompt_user}
                ],
                extra_headers=extra_headers if extra_headers else None,
                stream=True  
            )

            # Переменные для сборки текста и контроля частоты отправки в Telegram
            full_text = ""
            last_tg_update_time = time.time()
            # Символ "мигающего курсора" для красоты, пока текст пишется
            cursor = " ⏳" 

            # 6. Читаем чанки по мере их поступления от API
            async for chunk in response_stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    full_text += chunk.choices[0].delta.content
                    
                    current_time = time.time()
                    # Обновляем Telegram только если прошло больше 1.5 секунд
                    if current_time - last_tg_update_time > 1.5:
                        
                        # 1. РУЧНАЯ ПРОВЕРКА ДЛИНЫ СТРОКИ (> 3000 символов)
                        # Если текст уже большой, принудительно переносим его в новое сообщение,
                        # не дожидаясь ошибки от самого Telegram.
                        LEN_LIMIT = 1000
                        if len(full_text) > LEN_LIMIT:
                            logger.info(f"Длина текста превысила {LEN_LIMIT} символов. Принудительный перенос.")
                            try:
                                await message.edit_text(text=full_text)  # Фиксируем без курсора
                            except Exception:
                                pass
                            
                            # Открываем новое сообщение для стриминга (без Markdown)
                            message = await message.reply_text(text="⏳ Продолжение расклада...\n" + cursor)
                            full_text = chunk.choices[0].delta.content  # Сбрасываем буфер
                            last_tg_update_time = current_time
                            continue  # Переходим к следующему чанку

                        # 2. ОТПРАВКА ОБНОВЛЕНИЯ С ОБРАБОТКОЙ ОШИБОК
                        try:
                            await message.edit_text(text=full_text + cursor)
                        
                        except RetryAfter as flood_err:
                            # Ловим 429 ошибку (Too Many Requests). 
                            # Спим столько секунд, сколько просит Telegram, и продолжаем
                            logger.warning(f"Поймали 429 Flood Control. Спим {flood_err.retry_after} сек.")
                            await asyncio.sleep(flood_err.retry_after)
                            
                        except BadRequest as tg_err:
                            err_msg = str(tg_err).lower()
                            
                            # На всякий случай оставляем авто-подстраховку от лимита Telegram
                            if "message is too long" in err_msg or "message_too_long" in err_msg:
                                logger.info("Текст превысил лимит Telegram (авто-перехват). Перенос.")
                                try:
                                    await message.edit_text(text=full_text)
                                except Exception:
                                    pass
                                
                                message = await message.reply_text(text="⏳ Продолжение расклада...\n" + cursor)
                                full_text = chunk.choices[0].delta.content
                            
                            elif "too many requests" in err_msg or "retry after" in err_msg:
                                # Дополнительный перехват 429, если она прилетела как BadRequest
                                logger.warning("Поймали 429 ошибку в блоке BadRequest. Пропускаем итерацию.")
                                await asyncio.sleep(2)
                                
                            elif "message is not modified" in err_msg:
                                pass
                            
                            else:
                                logger.warning(f"Ошибка редактирования TG: {tg_err}")
                        
                        last_tg_update_time = current_time

            # 7. Финальное обновление ПОСЛЕДНЕГО сообщения (убираем курсор)
            try:
                await message.edit_text(text=full_text, parse_mode="Markdown")
            except BadRequest as tg_err:
                if "message is not modified" not in str(tg_err).lower():
                    # Если Markdown всё-таки упал в конце (например, ИИ не закрыл *),
                    # отправляем как обычный текст, чтобы пользователь не остался без ответа
                    await message.edit_text(text=full_text, parse_mode=None)

            # При стриминге некоторые провайдеры/прокси не отдают usage в конце.
            # На всякий случай считаем приблизительно или берем из последнего чанка, если он там есть
            prompt_tokens = 0
            completion_tokens = 0
            total_tokens = 0
            
            # 8. Обновляем лог — УСПЕХ
            ai_log.response_text = full_text
            ai_log.prompt_tokens = prompt_tokens
            ai_log.completion_tokens = completion_tokens
            ai_log.total_tokens = total_tokens
            ai_log.status = AIReadingInterpretation.AIStatus.SUCCESS
            await ai_log.asave()

            logger.info(f"Стриминг ИИ #{ai_log.id} успешно завершен.")
            return full_text

        except Exception as e:
            # 9. Обновляем лог — ОШИБКА
            logger.error(f"Ошибка при стриминге API ИИ в логе #{ai_log.id}: {e}", exc_info=True)
            
            ai_log.status = AIReadingInterpretation.AIStatus.FAILED
            ai_log.error_message = str(e)
            await ai_log.asave()

            # 10. ПРОВЕРКА ЛИМИТОВ КЛЮЧА
            # Переводим текст ошибки в нижний регистр для надежного поиска совпадений
            err_msg = str(e).lower()
            
            # Признаки того, что ключ исчерпан (429, 403, quota, rate limit)
            is_quota_error = any(
                phrase in err_msg 
                for phrase in ["429", "rate_limit", "quota", "too many requests", "exhausted", "limit exceeded"]
            )
            
            if is_quota_error:
                logger.warning(f"💥 Обнаружено исчерпание лимитов для ключа #{active_key.id}! Отключаем.")
                try:
                    # Помечаем в БД, что лимит исчерпан
                    active_key.is_exhausted = True
                    # Опционально: можно заблокировать на 1 час вперед, если нужно
                    # from django.utils import timezone
                    # api_key_obj.exhausted_until = timezone.now() + timezone.timedelta(hours=1)
                    
                    await active_key.asave()
                    logger.info(f"Ключ #{active_key.id} успешно переведен в статус is_exhausted=True")
                except Exception as db_err:
                    logger.error(f"Не удалось обновить статус API ключа в БД: {db_err}")

            # Пробрасываем ошибку дальше, чтобы сработал верхний уровень (например, возврат кнопки пользователю)
            raise e
        
    async def handle_ai_reading_callback(self, update: Update, context: CallbackContext):
        query = update.callback_query
        await query.answer()
        logger.info(f"Получен callback-запрос ИИ: {query.data}")

        reply_markup = query.message.reply_markup

        # Внутренняя функция для быстрого обновления текста текущей кнопки
        async def update_button_text(new_text: str):
            if reply_markup and reply_markup.inline_keyboard:
                for row in reply_markup.inline_keyboard:
                    for button in row:
                        if button.callback_data == query.data:
                            button.text = new_text
                await query.edit_message_reply_markup(reply_markup=reply_markup)

        try:
            # 1. Проверяем валидность callback_data
            try:
                _, reading_id_str = query.data.split("_")
                reading_id = int(reading_id_str)
            except (ValueError, IndexError):
                raise ValueError("❌ Неверный формат данных")

            # 2. Проверяем наличие записи в базе
            try:
                reading = await UserReading.objects.aget(id=reading_id)
            except ObjectDoesNotExist:
                raise ValueError("❌ Расклад не найден")

            # 3. Проверяем доступность ИИ
            ai_enabled = await self.should_add_ai_button()
            if not ai_enabled:
                raise ValueError("⏳ ИИ занят")

            if not reply_markup or not reply_markup.inline_keyboard:
                return  # Если клавиатуры нет, делать нечего
            
            # СЛУЧАЙ, КОГДА ВСЕ ОК: удаляем кнопку и запускаем генерацию
            new_keyboard = []
            for row in reply_markup.inline_keyboard:
                new_row = [button for button in row if button.callback_data != query.data]
                if new_row:
                    new_keyboard.append(new_row)
                    
            updated_markup = InlineKeyboardMarkup(new_keyboard)
            await query.edit_message_reply_markup(reply_markup=updated_markup)
            
            message = await query.message.reply_text('⏳ Начинаем генерацию...')
            await self.run_ai_interpretation(reading, message)

        except ValueError as e:
            logger.warning(f"Бизнес-ошибка в callback ИИ ({query.data}): {e}")
            # Вызываем внутреннюю функцию
            await update_button_text(str(e))

        except Exception as e:
            logger.error(f"Критическая ошибка при обработке запроса к ИИ: {e}", exc_info=True)
            # Вызываем внутреннюю функцию для системной ошибки
            await update_button_text("💥 Ошибка ИИ")
            