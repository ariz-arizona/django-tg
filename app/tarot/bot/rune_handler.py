import re
import os
from typing import List, Optional, Dict

import textwrap
import time
import json
import asyncio
import redis.asyncio as aioredis
import aiohttp
import random
from io import BytesIO
from bs4 import BeautifulSoup
from openai import AsyncOpenAI

from telegram import (
    Update,
    InputMediaPhoto,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Message,
)
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
from tg_bot.models import TgUser, Bot
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
    AIApiKey,
)
from tg_bot.models import BotFileCache
from server.logger import logger
from django.conf import settings

from tarot.utils.image_utils import create_spread_image
from tarot.bot.allcard_handler import AllCardHandler
from tarot.bot.ai_interpret_handler import AIInterpretHandler

# Инициализируем асинхронный клиент
redis_client = aioredis.StrictRedis(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", 6379)),
    db=3,
    decode_responses=True,  # Рекомендуется: автоматически декодирует bytes в строки python
)
redis_client_bot = aioredis.StrictRedis(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", 6379)),
    db=2,
    decode_responses=True,  # Рекомендуется: автоматически декодирует bytes в строки python
)


class RuneHandler:
    """Обработчик всех функций, связанных с рунами."""

    def __init__(self, bot_instance):
        self.bot = bot_instance

    def get_handlers(self):
        return [
            MessageHandler(
                filters.COMMAND
                & filters.TEXT
                & filters.ChatType.PRIVATE
                & filters.Regex(r"^\/fut(h?)ark( triplet)?$"),
                self.handle_futark,
            ),
            CallbackQueryHandler(
                self.handle_futark_callback,
                pattern=r"^futhark_",
            ),
        ]
        
    def paginate_text(self, text, chunk_size=600):
        # Разбиваем текст по 600 символов, стараясь не разрывать слова
        return textwrap.wrap(text, chunk_size, replace_whitespace=False, drop_whitespace=False)
    
    async def get_rune_paged_and_keyboard(self, reading_id, position, page):
        """
        Автономная функция: сама идет в БД, получает данные и формирует всё для ответа.
        """
        # 1. Забираем расклад из БД
        reading = await UserReading.objects.aget(id=reading_id)
        
        # 2. Находим данные текущей руны
        rune_data = next((item for item in reading.card_ids if item["position"] == position), None)
        if not rune_data:
            return "Ошибка: руна не найдена.", None
            
        rune = await Rune.objects.aget(id=rune_data["id"])
        inverted = rune_data["inverted"]

        # 3. Формируем текст
        if not inverted:
            keys = rune.straight_keys
            meaning = rune.straight_meaning
            position_text = getattr(rune, f"straight_pos_{position}")
        else:
            keys = rune.inverted_keys or f"для прямой руны: {rune.straight_keys}"
            meaning = rune.inverted_meaning or f"для прямой руны: {rune.straight_meaning}"
            position_text = getattr(rune, f"inverted_pos_{position}", None) or f"для прямой руны: {getattr(rune, f'straight_pos_{position}')}"

        full_text = (
            f"<b>{rune.symbol}</b> {rune.type}{' (Перевернуто)' if inverted else ''}"
            f"\n\nКлючи: {keys}"
            f"\n\nЗначение: {meaning}"
            f"\n\nПоложение: {position_text}"
        )

        # 4. Пагинация
        pages = textwrap.wrap(full_text, 600, replace_whitespace=False, drop_whitespace=False)
        current_page = max(0, min(page, len(pages) - 1))
        
        # 5. Формируем клавиатуру
        keyboard = []
        
        # Ряд 1: Навигация по тексту
        nav_row = [
            InlineKeyboardButton("⬅️" if current_page > 0 else "⚪", 
                                callback_data=f"futhark_{reading_id}_{position}_{current_page-1}" if current_page > 0 else "futhark_ignore"),
            InlineKeyboardButton(f"{current_page + 1} / {len(pages)}", callback_data="futhark_ignore"),
            InlineKeyboardButton("➡️" if current_page < len(pages) - 1 else "⚪", 
                                callback_data=f"futhark_{reading_id}_{position}_{current_page+1}" if current_page < len(pages) - 1 else "futhark_ignore")
        ]
        keyboard.append(nav_row)

        # Ряд 2: Выбор руны (идём по списку card_ids из расклада)
        runes_row = []
        for item in reading.card_ids:
            pos = item["position"]
            r = await Rune.objects.aget(id=item["id"])
            # Определяем символ: если это текущая позиция, выделяем скобками
            symbol_text = f"[{r.symbol}]" if item["position"] == position else r.symbol
            
            # Добавляем эмодзи 🔄, если руна перевернута
            is_inverted = item.get("inverted", False)
            emoji = " 🔄" if is_inverted else ""
            
            # Формируем итоговый текст кнопки: [Символ] Название 🔄
            btn_text = f"{symbol_text} {r.type}{emoji}"
            
            runes_row.append(InlineKeyboardButton(btn_text, callback_data=f"futhark_{reading_id}_{pos}_0"))
        
        keyboard.append(runes_row)

        return pages[current_page], InlineKeyboardMarkup(keyboard)
    
    async def handle_futark(self, update: Update, context: CallbackContext):
        """
        Обработчик команды /futark.
        """
        msg_text = update.message.text
        user = await self.bot.get_or_create_tg_user(update)
        logger.info(f"Обработка команды /futark с текстом: {msg_text[:100]}")
        
        category = UserReading.ReadingCategory.RUNES
        is_locked = await self.bot.check_reading_cooldown(update, category)
        if is_locked:
            return

        try:
            # Получаем все руны из базы данных
            runes = [rune async for rune in Rune.objects.all()]

            if "triplet" in msg_text.lower():
                # Выбираем 3 случайные руны
                raw_selected = random.sample(runes, 3)
                
                selected_runes_full_data = []
                for i, rune in enumerate(raw_selected):
                    selected_runes_full_data.append({
                        "id": rune.id,
                        "inverted": random.choice([True, False]),
                        "rune_obj": rune,
                        "position": i + 1
                    })

                # 3. Подготавливаем данные для JSON (только ID и статус)
                ids_for_db = [
                    {"id": item["id"], "inverted": item["inverted"], "position": item["position"]} 
                    for item in selected_runes_full_data
                ]
                
                rune_texts = []
                
                reading = await self.bot.save_reading(
                    user=user,
                    message_id=update.effective_message.message_id,
                    text=" ".join(rune_texts),
                    category=category,
                    card_ids=ids_for_db,
                    ids_for_db=True,
                    count=3
                )


                for i, item in enumerate(selected_runes_full_data):
                    rune = item["rune_obj"]
                    inverted = item["inverted"]
                    
                    # Формируем текст сообщения
                    rune_texts.append(
                        f"<b>{rune.symbol}</b> {rune.type}{' (Перевернутая)' if inverted else ''}"
                    )
                    
                _, reply_markup = await self.get_rune_paged_and_keyboard(reading.id, 1, 0)
                rows = reply_markup.inline_keyboard
                second_row_markup = InlineKeyboardMarkup([rows[1]])
                # Отправляем сообщение с рунами и inline-клавиатурой
                await update.message.reply_text(
                    "\n".join(rune_texts),
                    parse_mode=ParseMode.HTML,
                    reply_markup=second_row_markup,
                )
            else:
                # Выбираем одну случайную руну
                random_rune = random.choice(runes)
                inverted = "flip" in msg_text.lower() and random.choice([True, False])
                
                # Формируем текст сообщения
                text_parts = [f"<b>{random_rune.symbol}</b>", random_rune.type]
                if inverted:
                    text_parts.append("Перевернуто")

                reading = await self.bot.save_reading(
                    user=user,
                    message_id=update.effective_message.message_id,
                    text=" ".join(text_parts),
                    category=UserReading.ReadingCategory.RUNES,
                    card_ids=random_rune.id,
                    is_flipped_allowed=True,
                    count=1
                )

                # Отправляем текст и стикер
                await update.message.reply_text(
                    "\n".join(text_parts), parse_mode=ParseMode.HTML
                )
                await update.message.reply_sticker(random_rune.sticker)

        except Exception as e:
            logger.error(f"Ошибка при обработке команды /futark: {e}", exc_info=True)
            await update.message.reply_text(
                "Произошла ошибка при обработке вашего запроса. Пожалуйста, попробуйте снова."
            )

    async def handle_futark_callback(self, update: Update, context: CallbackContext):
        """
        Обработчик callback-запросов для рун.
        """
        query = update.callback_query
        await query.answer()
        if query.data == 'futhark_ignore':
            return
        try:
            _, reading_id, position, page = query.data.split("_")
            position = int(position)
            page = int(page)
            
            text, reply_markup = await self.get_rune_paged_and_keyboard(reading_id, int(position), int(page))
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Ошибка при обработке callback-запроса: {e}", exc_info=True)
            await query.edit_message_text(
                "Произошла ошибка. Пожалуйста, попробуйте снова."
            )


