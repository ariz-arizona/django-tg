import re
import os
from typing import List, Optional, Dict

import textwrap
import redis.asyncio as aioredis
import random

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    MessageHandler,
    CallbackQueryHandler,
    CallbackContext,
    filters,
)
from telegram.constants import ParseMode

from tg_bot.models import TgUser, Bot
from tarot.models import (
    Rune,
    UserReading,
)
from server.logger import logger
from django.conf import settings

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
                filters.TEXT 
                & filters.ChatType.PRIVATE
                & filters.Regex(r"^(?i)(/futhark(?:_triplet)?|рун[аы](?:\s+триплет)?)$"),
                self.handle_rune_reading, # Ваш новый метод в RuneHandler
            ),
            CallbackQueryHandler(
                self.handle_rune_callback, # Ваш новый метод в RuneHandler
                pattern=r"^futhark_",
            ),
        ]
        
    def paginate_text(self, text, chunk_size=600):
        # Разбиваем текст по 600 символов, стараясь не разрывать слова
        return textwrap.wrap(text, chunk_size, replace_whitespace=False, drop_whitespace=False)
    
    async def get_rune_paged_and_keyboard(self, reading_id, position=None, page=0):
        # 1. Забираем расклад из БД
        reading = await UserReading.objects.aget(id=reading_id)
        
        # Если позиция не передана, показываем общую информацию о раскладе
        if position is None:
            # Сначала собираем названия/символы для текста
            rune_list_text = []
            for item in reading.card_ids:
                r = await Rune.objects.aget(id=item["id"])
                # Добавляем эмодзи переворота, если нужно
                emoji = " 🔄" if item.get("inverted") else ""
                rune_list_text.append(f"{r.symbol} {r.type}{emoji}")
            
            full_text = (
                f"Выберите руну для просмотра подробного значения:\n\n"
                f"{chr(10).join(rune_list_text)}"
            )
            pages = [full_text]
            keyboard = []
        else:
            # 2. Находим данные текущей руны (только если позиция есть)
            rune_data = next((item for item in reading.card_ids if item["position"] == position), None)
            rune = await Rune.objects.aget(id=rune_data["id"])
            inverted = rune_data["inverted"]

            # 3. Формируем текст руны
            keys = rune.straight_keys if not inverted else (rune.inverted_keys or f"прямая: {rune.straight_keys}")
            meaning = rune.straight_meaning if not inverted else (rune.inverted_meaning or f"прямая: {rune.straight_meaning}")
            pos_attr = f"straight_pos_{position}" if not inverted else f"inverted_pos_{position}"
            pos_text = getattr(rune, pos_attr, None) or f"прямая: {getattr(rune, f'straight_pos_{position}')}"

            full_text = (
                f"<b>{rune.symbol}</b> {rune.type}{' (Перевернуто)' if inverted else ''}"
                f"\n\n<b>Ключи</b>: {keys}"
                f"\n\n<b>Значение</b>: {meaning}"
                f"\n\n<b>Положение</b>: {pos_text}"
            )
            pages = self.paginate_text(full_text)
            current_page = max(0, min(page, len(pages) - 1))
            
            # Навигация есть только если руна выбрана
            keyboard = []
            nav_row = [
                InlineKeyboardButton("⬅️" if current_page > 0 else "⚪", callback_data=f"futhark_{reading_id}_{position}_{current_page-1}" if current_page > 0 else "futhark_ignore"),
                InlineKeyboardButton(f"{current_page + 1} / {len(pages)}", callback_data="futhark_ignore"),
                InlineKeyboardButton("➡️" if current_page < len(pages) - 1 else "⚪", callback_data=f"futhark_{reading_id}_{position}_{current_page+1}" if current_page < len(pages) - 1 else "futhark_ignore")
            ]
            keyboard.append(nav_row)

        # 4. Ряд кнопок с рунами (формируется всегда)
        runes_row = []
        for item in reading.card_ids:
            r = await Rune.objects.aget(id=item["id"])
            
            # НЕТ ВЫДЕЛЕНИЯ, если position is None
            symbol_text = r.symbol
            if position is not None and item["position"] == position:
                symbol_text = f"[{r.symbol}]"
            
            is_inverted = item.get("inverted", False)
            emoji = " 🔄" if is_inverted else ""
            btn_text = f"{symbol_text} {r.type}{emoji}"
            
            runes_row.append(InlineKeyboardButton(btn_text, callback_data=f"futhark_{reading_id}_{item['position']}_0"))
        
        keyboard.append(runes_row)

        return pages[0] if position is None else pages[current_page], InlineKeyboardMarkup(keyboard)
    
    async def handle_rune_reading(self, update: Update, context: CallbackContext):
        """
        Обработчик команды /futark.
        """
        msg_text = update.message.text
        user = await self.bot.get_or_create_tg_user(update)
        is_triplet = "triplet" in msg_text or "триплет" in msg_text
        logger.info(f"Обработка команды /futark с текстом: {msg_text[:100]}")
        
        category = UserReading.ReadingCategory.RUNES
        is_locked = await self.bot.check_reading_cooldown(update, category)
        if is_locked:
            return

        try:
            # Получаем все руны из базы данных
            runes = [rune async for rune in Rune.objects.all()]

            if is_triplet:
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

                text, markup = await self.get_rune_paged_and_keyboard(reading.id, position=None)
                # Отправляем сообщение с рунами и inline-клавиатурой
                await update.message.reply_text(
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=markup,
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

    async def handle_rune_callback(self, update: Update, context: CallbackContext):
        """
        Обработчик callback-запросов для рун.
        """
        query = update.callback_query
        logger.info(f"Получен callback-запрос: {query.data}")
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


