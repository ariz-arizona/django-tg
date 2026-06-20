import re
import os
from typing import List, Optional, Dict
            
import json
import redis.asyncio as aioredis
import aiohttp
import random
from bs4 import BeautifulSoup

from telegram import Update, InputMediaPhoto, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Message
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
)
from tg_bot.models import BotFileCache
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

class OhHandler:
    """Обработчик всех функций, связанных с командой oh."""

    def __init__(self, bot_instance):
        self.bot = bot_instance

    def get_handlers(self):
        return [
            CommandHandler(["onehand", "oh"], self.handle_onehand, filters.ChatType.PRIVATE),
            CallbackQueryHandler(self.handle_onehand_callback, pattern=r"^oh_"),
        ]
        
    
    async def handle_onehand(self, update: Update, context: CallbackContext):
        """Точка входа: /onehand"""
        keyboard = [
            [
                InlineKeyboardButton("🃏 Таро", callback_data="oh_type_card"),
                InlineKeyboardButton("🖼️ Холст", callback_data="oh_type_spread"),
            ],
            [
                InlineKeyboardButton("🌟 Оракул", callback_data="oh_type_oraculum"),
                InlineKeyboardButton("🛡️ Футарк", callback_data="oh_type_futark"),
            ],
            [InlineKeyboardButton("🎴 Одна карта", callback_data="oh_type_one")],
        ]
        await update.message.reply_text(
            "Выберите тип гадания:", reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def handle_onehand_callback(self, update: Update, context: CallbackContext):
        query = update.callback_query
        await query.answer()
        data = query.data.split("_")  # oh_этап_значение_доп
        action = data[1]

        # 1. Выбор типа (Таро/Оракул и т.д.)
        if action == "type":
            gtype = data[2]
            context.user_data["oh_cmd"] = {"type": gtype, "params": []}
            if gtype in ["one", "spread"]:
                return await self._oh_finish(query, context)
            elif gtype == "futark":
                # Спец. меню для рун: 1 руна или триплет
                return await self._oh_ask_futhark_mode(query, context)
            return await self._oh_ask_count(query, context)

        # 2. Выбор количества (1-9)
        elif action == "count":
            gtype = context.user_data["oh_cmd"]["type"]
            val = data[2] 
            
            if gtype == "futark":
                # Сохраняем значение (1 или triplet) в параметры
                context.user_data["oh_cmd"]["params"].append(val)
                # Идем сразу на финиш, минуя flip
                return await self._oh_finish(query, context)
            
            # Для Таро/Оракула — логика прежняя
            context.user_data["oh_cmd"]["params"].append(val)
            return await self._oh_ask_deck(query, context)

        # 3. Выбор колоды (логика с вызовом вашего метода в режиме return)
        elif action == "deck":
            sub_action = data[2]

            if sub_action in ["choose", "page"]:
                page = int(data[3]) if sub_action == "page" else 0
                gtype = context.user_data["oh_cmd"]["type"]
                dtype = "oraculum" if gtype == "oraculum" else "tarot"

                decks, current, total = await self.make_decks_page(
                    current_page=page, items_per_page=6, deck_type=dtype, mode="return"
                )

                keyboard = await self._render_deck_keyboard(decks, current, total)
                
                await query.edit_message_text(
                    f"Выберите ID колоды (стр {current+1}/{total}):",
                    reply_markup=keyboard,
                )
                return

            elif sub_action == "set":
                deck_id = data[3]
                context.user_data["oh_cmd"]["params"].append(f"deck {deck_id}")
                return await self._oh_ask_flip(query, context)
            
            elif sub_action == "skip":
                return await self._oh_ask_flip(query, context)

        # 4. Перевернутые
        elif action == "flip":
            gtype = context.user_data["oh_cmd"]["type"]
            is_yes = data[2] == "yes"
            
            if gtype == "futark":
                # Если выбрали "Да" для перевернутых, добавляем "flip" для команды /futark
                if is_yes:
                    context.user_data["oh_cmd"]["params"].append("flip")
                return await self._oh_finish(query, context)
            
            if is_yes:
                context.user_data["oh_cmd"]["params"].append("flip")

            if context.user_data["oh_cmd"]["type"] == "card":
                return await self._oh_ask_major(query, context)
            
            return await self._oh_finish(query, context)

        # 5. Старшие арканы
        elif action == "major":
            if data[2] == "yes":
                context.user_data["oh_cmd"]["params"].append("major")
            return await self._oh_finish(query, context)

        # 6. Финиш
        elif action == "run_go":
            await query.edit_message_text("Команда запущена.")
            context.user_data.pop("oh_cmd", None)

    # --- Вспомогательные экраны ---
    async def _oh_ask_count(self, query, context):
        # Создаем все кнопки
        all_btns = [
            InlineKeyboardButton(str(i), callback_data=f"oh_count_{i}")
            for i in range(1, 10)
        ]
        
        # Разбиваем на ряды по 3 элемента: [all_btns[i:i+3] for i in range(0, len(all_btns), 3)]
        # Это создаст список списков: [[1,2,3], [4,5,6], [7,8,9]]
        keyboard_rows = [all_btns[i : i + 3] for i in range(0, len(all_btns), 3)]
        
        await query.edit_message_text(
            "Количество карт:", 
            reply_markup=InlineKeyboardMarkup(keyboard_rows)
        )
        
    async def _oh_ask_futhark_mode(self, query, context):
        kb = [[InlineKeyboardButton("Одна руна", callback_data="oh_count_1"), 
            InlineKeyboardButton("Триплет", callback_data="oh_count_triplet")]]
        await query.edit_message_text("Выберите расклад:", reply_markup=InlineKeyboardMarkup(kb))
        
    async def _oh_ask_deck(self, query, context):
        kb = [
            [
                InlineKeyboardButton("Нет", callback_data="oh_deck_skip"),
                InlineKeyboardButton("Выбрать...", callback_data="oh_deck_choose"),
            ]
        ]
        await query.edit_message_text(
            "Нужна определенная колода?", reply_markup=InlineKeyboardMarkup(kb)
        )
        
    async def _render_deck_keyboard(self, decks, current, total):
        keyboard = []
        # Разбиваем 6 колод на ряды по 2 кнопки (получится 3 ряда)
        # Это удобно для "одной руки" — кнопки крупные и легко попадать большим пальцем
        row = []
        async for deck in decks:
            row.append(
                InlineKeyboardButton(str(deck.name), callback_data=f"oh_deck_set_{deck.id}")
            )
            if len(row) == 2:  # По 2 кнопки в ряду
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        # Пагинация (остается без изменений)
        nav = []
        if current > 0:
            nav.append(InlineKeyboardButton("⬅️", callback_data=f"oh_deck_page_{current - 1}"))
        nav.append(InlineKeyboardButton("❌ Отмена", callback_data="oh_deck_skip"))
        if current < total - 1:
            nav.append(InlineKeyboardButton("➡️", callback_data=f"oh_deck_page_{current + 1}"))
        keyboard.append(nav)
        
        return InlineKeyboardMarkup(keyboard)
    
    async def _oh_ask_flip(self, query, context):
        kb = [
            [
                InlineKeyboardButton("Да", callback_data="oh_flip_yes"),
                InlineKeyboardButton("Нет", callback_data="oh_flip_no"),
            ]
        ]
        await query.edit_message_text(
            "Использовать перевернутые карты?", reply_markup=InlineKeyboardMarkup(kb)
        )

    async def _oh_ask_major(self, query, context):
        kb = [
            [
                InlineKeyboardButton("Да", callback_data="oh_major_yes"),
                InlineKeyboardButton("Нет", callback_data="oh_major_no"),
            ]
        ]
        await query.edit_message_text(
            "Брать только старшие арканы?", reply_markup=InlineKeyboardMarkup(kb)
        )

    async def _oh_finish(self, query, context):
        cmd = context.user_data["oh_cmd"]
        gtype = cmd['type']
        params = cmd['params']

        # 1. Логика для Футарка
        if gtype == "futark":
            # Ищем, есть ли в параметрах 'triplet' (мы его добавляли в _oh_ask_count)
            # Если "triplet" в params — используем команду /futhark_triplet, иначе /futhark
            if "triplet" in params:
                base_cmd = "/futhark triplet"
                # Удаляем маркер triplet из списка параметров, чтобы он не дублировался
                params = [p for p in params if p != "triplet"]
            else:
                base_cmd = "/futhark"
            
            # Если есть flip, добавляем его к команде
            final_cmd = f"{base_cmd}"

        # 2. Логика для Таро/Оракула
        else:
            # Получаем количество из параметров (если оно там есть)
            count = ""
            for p in params:
                if p.isdigit():
                    count = p
                    params.remove(p)
                    break
            
            # Формируем: /type + count + остальные параметры
            final_cmd = f"/{gtype}{count} {' '.join(params)}".strip().replace("  ", " ")

        context.user_data.pop("oh_cmd", None)
        # Удаляем инлайн-меню
        await query.delete_message()

        # Создаем пользовательскую клавиатуру с одной кнопкой-командой
        # one_time_keyboard=True скроет её после нажатия
        reply_keyboard = ReplyKeyboardMarkup(
            [[final_cmd]], one_time_keyboard=True, resize_keyboard=True
        )

        await query.message.reply_text(
            f"Команда готова: <code>{final_cmd}</code>\n\n"
            "Нажмите кнопку, чтобы отправить.",
            reply_markup=reply_keyboard,
            parse_mode="HTML"
        )
