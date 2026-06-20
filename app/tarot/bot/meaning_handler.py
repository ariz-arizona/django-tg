import re
import os
from typing import List, Optional, Dict

import asyncio
import json
import redis.asyncio as aioredis
from aiohttp import ClientError, ClientTimeout, ClientSession
import random
from bs4 import BeautifulSoup
import logging
from html import escape

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
    after_log
)
from telegram import (
    Update, InputMediaPhoto, InlineKeyboardButton,
    InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    )
from telegram.ext import (
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    CallbackContext,
    filters,
)
from telegram.constants import ParseMode
from django.core.exceptions import ObjectDoesNotExist

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
    UserReading,
    TarotMeaningCategory,
)
from tg_bot.models import BotFileCache
from server.logger import logger
from django.conf import settings

from tarot.utils.random import get_random_icon

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

class MeaningHandler:
    """Обработчик трактовок карт и навигации по ним."""

    def __init__(self, bot_instance):
        """
        Инициализация обработчика трактовок.

        Args:
            bot_instance: Экземпляр основного бота для доступа к методам split_text и другим утилитам
        """
        self.bot = bot_instance

    @property
    def app_bot_id(self):
        """Проксируем доступ к app_bot_id через основной инстанс бота."""
        return self.bot.app_bot_id

    def get_handlers(self):
        return [
            CallbackQueryHandler(
                self.handle_desc_button, pattern=r"^desc_(\d+(?:#\d+)*)$"
            ),
            CallbackQueryHandler(
                self.handle_pagination, pattern=r"^meaning_"
            ),
        ]
        
        
    def split_text(self, text, chunk_size=1024):
        # Шаг 1: разбиваем по строкам (как у тебя)
        lines = text.split("\n")
        chunks = []
        current_chunk = ""

        for line in lines:
            needed_length = len(line)
            if current_chunk:
                needed_length += 1  # символ '\n'

            if len(current_chunk) + needed_length > chunk_size:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = line
            else:
                if current_chunk:
                    current_chunk += "\n" + line
                else:
                    current_chunk = line

        if current_chunk:
            chunks.append(current_chunk)

        # Шаг 2: теперь разбиваем каждый чанк, если он > chunk_size, по словам (~1024)
        final_chunks = []

        for chunk in chunks:
            if len(chunk) <= chunk_size:
                final_chunks.append(chunk)
            else:
                # Разбиваем большой чанк по словам, стараясь не превышать chunk_size
                words = chunk.split(' ')
                temp_chunk = ""
                for word in words:
                    # Проверяем, поместится ли слово в текущий подчанк
                    test_chunk = temp_chunk + (" " if temp_chunk else "") + word
                    if len(test_chunk) <= chunk_size:
                        temp_chunk = test_chunk
                    else:
                        # Слово не помещается — сохраняем текущий подчанк и начинаем новый
                        if temp_chunk:
                            final_chunks.append(temp_chunk)
                        temp_chunk = word  # начинаем с текущего слова
                # Добавляем последний подчанк
                if temp_chunk:
                    final_chunks.append(temp_chunk)

        # Шаг 3: массив уже плоский — ничего дополнительно "выравнивать" не нужно,
        # потому что мы добавляли строки напрямую в final_chunks

        return final_chunks
        
    async def create_pagination_keyboard(
        self, reading_id, current_card_idx, meaning_type, current_page, total_pages, card_ids
    ):
        try:
            card_id = card_ids[current_card_idx]
            # Загружаем категории
            all_meanings_qs = ExtendedMeaning.objects.prefetch_related(
                "category_base"
            ).filter(tarot_card__card_id=card_id)

            # Собираем все значения в список через async for
            meanings_list = []
            async for item in all_meanings_qs:
                # Предполагаем, что у item есть category_base
                # Используем str() для ID, чтобы корректно сравнивать с meaning_type
                cat_id = str(item.category_base.id)
                cat_name = item.category_base.name
                meanings_list.append((cat_id, cat_name))
            
            meanings_list.append(("base", "Базовый"))
            meanings_list.sort(key=lambda x: x[1])

            curr_m_idx = next((i for i, m in enumerate(meanings_list) if m[0] == str(meaning_type)), 0)
            # Индексы соседей
            idx_prev = current_card_idx - 1
            idx_next = current_card_idx + 1

            # Получаем имена для кнопок (если они существуют)
            # Внимание: для простоты запроса делаем асинхронный поиск
            name_prev = None
            if idx_prev >= 0:
                card_obj = await TarotCard.objects.aget(card_id=card_ids[idx_prev])
                name_prev = card_obj.name

            name_next = None
            if idx_next < len(card_ids):
                card_obj = await TarotCard.objects.aget(card_id=card_ids[idx_next])
                name_next = card_obj.name
            
            keyboard = []

            btn_prev_card = [f"{name_prev} ⬅️" if name_prev else get_random_icon(), 
                             f"meaning_{reading_id}_{idx_prev}_base_1" if name_prev else "meaning_ignore"]
            
            btn_next_card = [f"➡️ {name_next}" if name_next else get_random_icon(), 
                             f"meaning_{reading_id}_{idx_next}_base_1" if name_next else "meaning_ignore"]
            
            # Ряд 2: Типы трактовок
            prev_m = meanings_list[(curr_m_idx - 1) % len(meanings_list)]
            next_m = meanings_list[(curr_m_idx + 1) % len(meanings_list)]
            
            # Ряд 3: Пагинация страниц
            has_prev_page = current_page > 1
            has_next_page = current_page < total_pages
            
            btn_prev_page = ["◀️", f"meaning_{reading_id}_{current_card_idx}_{meaning_type}_{current_page-1}"] if has_prev_page else [get_random_icon(), "meaning_ignore"]
            btn_next_page = ["▶️", f"meaning_{reading_id}_{current_card_idx}_{meaning_type}_{current_page+1}"] if has_next_page else [get_random_icon(), "meaning_ignore"]

            # --- Формирование клавиатуры ---
            keyboard = [
                # Ряд 1: Карты
                [InlineKeyboardButton(btn_prev_card[0], callback_data=btn_prev_card[1]),
                 InlineKeyboardButton(btn_next_card[0], callback_data=btn_next_card[1])],
                
                # Ряд 2: Типы трактовок
                [InlineKeyboardButton(f"« {prev_m[1]}", callback_data=f"meaning_{reading_id}_{current_card_idx}_{prev_m[0]}_1"),
                 InlineKeyboardButton(f"{next_m[1]} »", callback_data=f"meaning_{reading_id}_{current_card_idx}_{next_m[0]}_1")],
                
                # Ряд 3: Пагинация страниц
                [InlineKeyboardButton(btn_prev_page[0], callback_data=btn_prev_page[1]),
                 InlineKeyboardButton(f"{current_page} / {total_pages}", callback_data="meaning_ignore"),
                 InlineKeyboardButton(btn_next_page[0], callback_data=btn_next_page[1])]
            ]

            return InlineKeyboardMarkup(keyboard)

        except Exception as e:
            logger.error(f"Ошибка клавиатуры для расклада {reading_id}: {e}", exc_info=True)
            return None
        
    async def send_paginated_text(self, update: Update, reading_id, card_index, meaning_type, page):
        # 1. Получаем объект расклада, чтобы достать актуальные card_ids
        reading = await UserReading.objects.aget(id=reading_id)
        card_ids = [str(item.get("id")) for item in (reading.card_ids or [])]
        
        # 2. Получаем ID текущей карты из списка
        card_id = card_ids[card_index]
        
        # 3. Получаем текст и делим его
        text = await self.get_card_meaning(card_id, meaning_type)
        text_parts = self.split_text(text)
        
        # 4. ВЫЗОВ С 6 АРГУМЕНТАМИ
        keyboard = await self.create_pagination_keyboard(
            reading_id, 
            card_index, 
            meaning_type, 
            page, 
            len(text_parts), 
            card_ids  # <--- Добавили недостающий аргумент
        )
        
        # 5. Имя карты для заголовка
        base_card = await TarotCard.objects.aget(card_id=card_id)
        
        if meaning_type == "base":
            header = "Базовый смысл"
        else:
            category_obj = await TarotMeaningCategory.objects.aget(id=meaning_type)
            header = category_obj.name
        
        # Отправка
        await update.effective_message.reply_text(
            text=(
                f"<b>{base_card.name}</b>\n"
                f"{header}\n"
                f"стр {page}/{len(text_parts)}\n\n"
                f"{text_parts[page - 1]}"
            ),
            reply_markup=keyboard,
            reply_to_message_id=reading.message_id,
            parse_mode=ParseMode.HTML,
        )
    
    async def get_card_meaning(self, card_id: str, meaning_type: str) -> str:
        """
        Универсальный метод получения текста трактовки.
        """
        try:
            if meaning_type == "base":
                card = await TarotCard.objects.aget(card_id=card_id)
                return card.meaning
            
            # Предполагаем, что CategoryBase — имя модели, которое у тебя в коде
            meaning_obj = await ExtendedMeaning.objects.filter(
                tarot_card__card_id=card_id, 
                category_base__id=meaning_type
            ).aget()
            
            return meaning_obj.text
        except Exception as e:
            logger.error(f"Ошибка получения трактовки (card_id={card_id}, type={meaning_type}): {e}")
            return "Трактовка не найдена."
        
    async def handle_pagination(self, update: Update, context: CallbackContext):
        query = update.callback_query
        logger.info(f"Получен callback-запрос: {query.data}")
        
        # Разбор колбэка
        data = query.data.split("_")
        if data[1] == "ignore":
            await query.answer("Дальше пути нет...")
            return
            
        await query.answer()
        
        _, reading_id, card_idx, meaning_type, page = data
        
        card_idx, page = int(card_idx), int(page)

        # 1. Данные из БД
        reading = await UserReading.objects.aget(id=reading_id)
        card_ids = [str(item.get("id")) for item in (reading.card_ids or [])]
        card_id = card_ids[card_idx]
        
        # 2. Получение текста
        text = await self.get_card_meaning(card_id, meaning_type)
        text_parts = self.split_text(text)
        current_page = max(1, min(page, len(text_parts)))
        
        # 3. Имя карты и заголовок
        base_card = await TarotCard.objects.aget(card_id=card_id)
        
        if meaning_type == "base":
            header = "Базовый смысл"
        else:
            category_obj = await TarotMeaningCategory.objects.aget(id=meaning_type)
            header = category_obj.name

        # 4. Клавиатура (аргументы теперь совпадают 1 в 1)
        keyboard = await self.create_pagination_keyboard(
            reading_id, 
            card_idx, 
            meaning_type, 
            current_page, 
            len(text_parts), 
            card_ids
        )

        await query.edit_message_text(
            text=f"<b>{base_card.name}</b>\n<i>{header}</i>\nстр {current_page}/{len(text_parts)}\n\n{text_parts[current_page-1]}",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    async def handle_desc_button(self, update: Update, context: CallbackContext):
        query = update.callback_query
        await query.answer()
        logger.info(f"Получен callback-запрос: {query.data}")

        # Теперь data: "desc_{reading_id}"
        _, reading_id = query.data.split("_")
        
        reading = await UserReading.objects.aget(id=reading_id)
        # Получаем список ID карт из БД (обрабатываем формат словарей)
        card_ids = [str(item.get("id")) for item in (reading.card_ids or [])]
        
        logger.info(f"Запуск трактовки для расклада {reading_id}, карт: {len(card_ids)}")
        
                # 1. Получаем текущую клавиатуру
        keyboard = query.message.reply_markup.inline_keyboard
        new_keyboard = []
        found = False

        # 2. Ищем и удаляем кнопку
        for row in keyboard:
            new_row = [btn for btn in row if btn.callback_data != query.data]
            # Добавляем строку в новую клавиатуру, только если она не стала пустой
            if new_row:
                new_keyboard.append(new_row)
            if len(new_row) < len(row):
                found = True
        if found:
            await query.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(new_keyboard))
        
        # Запускаем пагинацию с 0-й карты
        await self.send_paginated_text(update, reading_id, 0, "base", 1)

