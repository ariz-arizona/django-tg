# ai_interpret_handler.py
import json
import os
from typing import List, Optional, Dict
import redis.asyncio as aioredis
import tiktoken
import time
import random
from openai import AsyncOpenAI

from telegram import Update,  Message, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    CallbackQueryHandler,
    CallbackContext,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, RetryAfter

from django.utils.timezone import now, timedelta
from django.core.exceptions import ObjectDoesNotExist

from tg_bot.bot.abstract import AbstractBot
from tg_bot.models import (
    TgUser, Bot
)
from tarot.models import (
    AIReadingPage,
    UserReading,
    AIReadingInterpretation,
    AIApiKey
)
from server.logger import logger
from django.conf import settings


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

def is_markup_identical(markup1, markup2):
    # Если оба None — они идентичны
    if markup1 is None and markup2 is None:
        return True
    # Если один None, а другой нет — они разные
    if markup1 is None or markup2 is None:
        return False
    
    # Большинство объектов в python-telegram-bot поддерживают прямое сравнение
    return markup1 == markup2

encoding = tiktoken.get_encoding("o200k_base") 

def count_tokens(text: str) -> int:
    return len(encoding.encode(text))

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
            # Навигация по уже сгенерированным страницам
            CallbackQueryHandler(
                self.handle_ai_navigation_callback, 
                pattern=r"^aipaged_",
            ),
        ]
        
    async def get_ai_paged_data(self, ai_log: AIReadingInterpretation, page_number: int):
        """
        Возвращает текст страницы и соответствующую клавиатуру.
        """
        await ai_log.arefresh_from_db()
        is_pending = (ai_log.status == AIReadingInterpretation.AIStatus.PENDING)
        
        # 1. Получаем все страницы из базы
        pages_qs = AIReadingPage.objects.filter(interpretation=ai_log).order_by("page_number")
        pages = []
        async for p in pages_qs:
            pages.append(p)
        total_pages = len(pages)
        
        # 2. Определение текста
        # Если страница существует в БД — берем ее
        if page_number < total_pages:
            text = pages[page_number].content
        else:
            # Если мы запрашиваем "будущую" страницу во время стриминга
            text = "⏳ Трактовка дополняется..."

        # 3. Генерация клавиатуры
        icons = ["✨", "🔮", "🌙", "🃏", "🕯️", "🌌"]
        random_icon = random.choice(icons)
        
        # Левая кнопка
        left_btn = (
            InlineKeyboardButton("◀", callback_data=f"aipaged_{ai_log.id}_{page_number - 1}") 
            if page_number > 0 
            else InlineKeyboardButton(random_icon, callback_data="aipaged_ignore")
        )

        # Средняя кнопка
        center_text = f"стр. {page_number + 1} из {total_pages if not is_pending else '...'}"
        center_btn = InlineKeyboardButton(center_text, callback_data="aipaged_ignore")

        # Правая кнопка
        # Если идет генерация или есть еще страницы — добавляем кнопку "Вперед"
        if is_pending or (page_number < total_pages - 1):
            right_btn = InlineKeyboardButton("▶", callback_data=f"aipaged_{ai_log.id}_{page_number + 1}")
        else:
            right_btn = InlineKeyboardButton(random_icon, callback_data="aipaged_ignore")

        keyboard = InlineKeyboardMarkup(inline_keyboard=[[left_btn, center_btn, right_btn]])
        
        return text, keyboard

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
                
            prompt_text = prompt_system + prompt_user
            prompt_tokens = len(encoding.encode(prompt_text))
            
            full_response_text = ""

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

            # Переменные для сборки текста
            buffer_text = ""          # Накапливаем текст для текущей страницы
            page_number = 0          
            page_counter = 0          
            chunk_counter = 0
            LEN_LIMIT = 500
            TG_UPDATE_INTERVAL = 1
            last_tg_update_time = time.time()
            
            prompt_tokens = 0
            completion_tokens = 0
            total_tokens = 0
            
            system_snippet = prompt_system[:100]
            if len(prompt_system) > 100:
                system_snippet += "..."
            
            # Если пользовательский запрос пустой, пишем "Пусто"
            user_text = prompt_user if prompt_user.strip() else "пусто"

            await message.edit_text(
                text=f"Системный промпт: {system_snippet}\n\nЗапрос: {user_text}"
            )

            # Читаем чанки по мере их поступления от API
            async for chunk in response_stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    full_response_text += content
                    
                    buffer_text += content
                    chunk_counter += 1
                    
                    # 1. Проверка лимита: если накопили 1000 — фиксируем страницу в БД
                    if len(buffer_text) >= LEN_LIMIT:
                        split_index = buffer_text.rfind(' ', 0, LEN_LIMIT)
                        split_index = split_index if split_index != -1 else LEN_LIMIT
                        
                        page_content = buffer_text[:split_index].strip()
                        buffer_text = buffer_text[split_index:].lstrip()
                        
                        await AIReadingPage.objects.acreate(
                            interpretation=ai_log,
                            content=page_content,
                            page_number=page_counter
                        )
                        page_counter += 1
                        chunk_counter = 0
                        # Теперь page_counter указывает на следующую (пустую) страницу

                    # 2. Обновление Telegram
                    current_time = time.time()
                    time_since_last_update = current_time - last_tg_update_time
                    is_first_chunk_of_page = (chunk_counter == 1)

                    if (
                            page_counter == 0 and
                            time_since_last_update > TG_UPDATE_INTERVAL and
                            len(buffer_text) > 10
                        ):
                        if buffer_text.strip():
                            try:
                                text, keyboard = await self.get_ai_paged_data(ai_log, page_number)
                                await message.edit_text(
                                    text=buffer_text.strip(), 
                                    reply_markup=keyboard
                                )
                                last_tg_update_time = current_time
                            except Exception as e:
                                if "message is not modified" not in str(e).lower():
                                    logger.warning(f"Ошибка обновления TG: {e}")
                    elif page_counter > 0 and is_first_chunk_of_page:
                        text, keyboard = await self.get_ai_paged_data(ai_log, page_number)
                        
                        try:
                            if not is_markup_identical(message.reply_markup, keyboard):
                                await message.edit_reply_markup(reply_markup=keyboard)
                        except BadRequest as e:
                            if "Message is not modified" not in str(e):
                                raise e
                if chunk.usage:
                    prompt_tokens = chunk.usage.prompt_tokens
                    completion_tokens = chunk.usage.completion_tokens
                    total_tokens = chunk.usage.total_tokens

            if buffer_text:
                await AIReadingPage.objects.acreate(
                    interpretation=ai_log,
                    content=buffer_text,
                    page_number=page_counter
                )
            
            if not completion_tokens:
                completion_tokens = len(encoding.encode(full_response_text))
                total_tokens = prompt_tokens + completion_tokens
            
            # 8. Обновляем лог — УСПЕХ
            ai_log.prompt_tokens = prompt_tokens
            ai_log.completion_tokens = completion_tokens
            ai_log.total_tokens = total_tokens
            ai_log.status = AIReadingInterpretation.AIStatus.SUCCESS
            await ai_log.asave()
            
            text, keyboard = await self.get_ai_paged_data(ai_log, page_number)
            await message.edit_text(text=text, reply_markup=keyboard)

            logger.info(f"Стриминг ИИ #{ai_log.id} успешно завершен.")
            return ai_log

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

        # Функция возвращает InlineKeyboardMarkup или None
        def get_keyboard_without_button() -> Optional[InlineKeyboardMarkup]:
            if not query.message.reply_markup or not query.message.reply_markup.inline_keyboard:
                return None
                
            new_keyboard = []
            for row in query.message.reply_markup.inline_keyboard:
                new_row = [btn for btn in row if btn.callback_data != query.data]
                if new_row:
                    new_keyboard.append(new_row)
            
            return InlineKeyboardMarkup(new_keyboard) if new_keyboard else None

        # Функция возвращает InlineKeyboardMarkup
        def get_keyboard_with_error(error_text: str, message: Message = None) -> Optional[InlineKeyboardMarkup]:
            if not query.message.reply_markup or not query.message.reply_markup.inline_keyboard:
                return None

            new_keyboard = []
            for row in query.message.reply_markup.inline_keyboard:
                new_row = []
                for btn in row:
                    if btn.callback_data == query.data:
                        new_callback = f"{btn.callback_data}_{message.message_id}" if message else btn.callback_data
                        new_row.append(InlineKeyboardButton(text=error_text, callback_data=new_callback))
                    else:
                        new_row.append(btn)
                new_keyboard.append(new_row)
            
            return InlineKeyboardMarkup(new_keyboard)

        async def handle_error(e: Exception, is_critical: bool):
            # 1. Логирование
            if is_critical:
                logger.error(f"Критическая ошибка: {e}", exc_info=True)
                display_btn_text = "💥 Ошибка"
                error_text = "❌ Произошел технический сбой. Пожалуйста, попробуйте создать новый расклад позже."
            else:
                logger.warning(f"Бизнес-ошибка ({query.data}): {e}")
                display_btn_text = "⏳ ИИ занят" if "занят" in str(e).lower() else "🔄 Ошибка обработки"
                error_text = "⚠️ К сожалению, ИИ сейчас не может выполнить запрос. Попробуйте чуть позже."

            # 2. Обновление клавиатуры
            error_markup = get_keyboard_with_error(display_btn_text, message)
            if not is_markup_identical(query.message.reply_markup, error_markup):
                await query.message.edit_reply_markup(reply_markup=error_markup)

            # 3. Обновление текста сообщения
            try:
                if message:
                    await message.edit_text(error_text)
                elif message_id:
                    await context.bot.edit_message_text(error_text, chat_id=query.message.chat_id, message_id=message_id)
                else:
                    await query.message.reply_text(error_text)
            except BadRequest as br:
                if "Message is not modified" not in str(br):
                    raise br
                
        message = None
        try:
            # 1. Валидация и проверка ИИ
            parts = query.data.split("_", 2)
        
            if len(parts) < 2:
                raise ValueError("❌ Неверный формат данных")
                
            reading_id = int(parts[1])
            message_id = int(parts[2]) if len(parts) > 2 else None
            
            reading = await UserReading.objects.aget(id=reading_id)
                
            if not await self.should_add_ai_button():
                raise ValueError("⏳ ИИ занят")

            # 2. УСПЕХ: Удаляем кнопку и запускаем процесс
            new_markup = get_keyboard_without_button()
            await query.edit_message_reply_markup(reply_markup=new_markup)
            message = await query.message.reply_text('⏳ Начинаем генерацию...')
            
            await self.run_ai_interpretation(reading, message)

        except ValueError as e:
            await handle_error(e, is_critical=False)
        except Exception as e:
            await handle_error(e, is_critical=True)
    
    async def handle_ai_navigation_callback(self, update: Update, context: CallbackContext):
        """Обработка кнопок пагинации (aipaged_)."""
        query = update.callback_query
        
        if query.data == 'aipaged_ignore':
            await query.answer()
            return
            
        _, ai_log_id, page_num = query.data.split("_")
        page_number = int(page_num)
        
        ai_log = await AIReadingInterpretation.objects.aget(id=ai_log_id)
        
        # Если статус PENDING, просто уведомляем
        if ai_log.status == AIReadingInterpretation.AIStatus.PENDING:
            await query.answer("⏳ Подождите, генерация еще идет...", show_alert=False)
            return

        text, keyboard = await self.get_ai_paged_data(ai_log, page_number)
        await query.edit_message_text(text=text, reply_markup=keyboard)
        await query.answer()