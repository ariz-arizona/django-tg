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

# ═══════════════════════════════════════════════════════════════
# КОНСТАНТЫ: тексты команд и пояснений
# ═══════════════════════════════════════════════════════════════

# --- Callback-префиксы и ключи ---
OH_PREFIX = "oh_"
OH_CMD = "oh_cmd"
OH_TYPE = "oh_type"
OH_CFG = "oh_cfg"
OH_RUNES = "oh_runes"

# --- Типы гаданий ---
TYPE_CARD = "card"
TYPE_ORACULUM = "oraculum"
TYPE_RUNES = "runes"
TYPE_CANVAS = "canvas"

# --- Параметры конфигурации ---
PARAM_COUNT = "count"
PARAM_DECK = "deck"
PARAM_MAJOR = "major"
PARAM_FLIP = "flip"

# --- Значения параметров ---
VAL_RANDOM = "random"
VAL_YES = "yes"
VAL_NO = "no"
VAL_GO = "go"
VAL_ONE = "one"
VAL_STICKER = "sticker"

VAL_RIDER = "waite"  
VAL_LENORMAND = "lenormand" 

# --- Колоды ---
DECK_RIDER = "deck_rider"
DECK_LENORMAND = "deck_lenormand"
DECK_MAJOR = "major"
DECK_FLIP = "flip"

# --- Команды ---
CMD_ONE = "/one"
CMD_TAROT = "/tarot"
CMD_FUTHARK = "/futhark"
CMD_FUTHARK_TRIPLET = "/futhark_triplet"

# --- Эмодзи-метки ---
MARK_CHECK = "✅ "
MARK_CARD = "🎴"
MARK_STICKER = "🃏"
MARK_TARO = "🔮"
MARK_ORACULUM = "🌟"
MARK_RUNES = "ᚠ"
MARK_CANVAS = "🖼️"
MARK_DICE = "🎲"
MARK_STAR = "⭐"
MARK_ALL = "🔮"
MARK_FLIP = "🔄"
MARK_STRAIGHT = "⬆️"
MARK_ROCKET = "🚀"
MARK_COUNT = "📊"

# --- Тексты кнопок ---
BTN_ONE_CARD = f"{MARK_CARD} Одна карта"
BTN_STICKER = f"{MARK_STICKER} Таро (стикер)"
BTN_TARO = f"{MARK_TARO} Таро"
BTN_ORACULUM = f"{MARK_ORACULUM} Оракул"
BTN_RUNES = f"{MARK_RUNES} Руны"
BTN_CANVAS = f"{MARK_CANVAS} Холст"
BTN_RANDOM = f"{MARK_DICE} Случайно"
BTN_RIDER = f"{MARK_STICKER} Райдер-Уэйт"
BTN_LENORMAND = f"{MARK_ORACULUM} Ленорман"
BTN_MAJOR_ONLY = f"{MARK_STAR} Только Старшие"
BTN_ALL_ARCANA = f"{MARK_ALL} Все арканы"
BTN_WITH_FLIP = f"{MARK_FLIP} С перевернутыми"
BTN_NO_FLIP = f"{MARK_STRAIGHT} Прямые"
BTN_GET_CMD = f"{MARK_ROCKET} Получить команду"
BTN_1_RUNE = "1 руна"
BTN_3_RUNES = "3 руны"

# --- Тексты сообщений ---
MSG_CHOOSE_TYPE = "Выберите тип гадания:"
MSG_UNKNOWN_CMD = "Неизвестная команда."
MSG_CONFIG_TITLE = "⚙️ Настройте расклад:\n\n"
MSG_COUNT_LABEL = f"{MARK_COUNT} Количество:"
MSG_DECK_LABEL_ORACULUM = f"{MARK_ORACULUM} Колода:"
MSG_DECK_LABEL_TARO = f"{MARK_STICKER} Колода:"
MSG_ARCANA_LABEL = f"{MARK_STAR} Арканы:"
MSG_FLIP_LABEL = f"{MARK_FLIP} Перевернутые:"
MSG_MAJOR_ONLY_TXT = "Только Старшие"
MSG_ALL_ARCANA_TXT = "Все"
MSG_FLIP_YES = "Да"
MSG_FLIP_NO = "Нет"
MSG_CHOOSE_RUNES = "Выберите расклад рун:"
MSG_CMD_READY_PREFIX = "Команда готова: <code>"
MSG_CMD_READY_SUFFIX = "</code>\n\nНажмите кнопку, чтобы отправить."
MSG_PREVIEW_PREFIX = "\n\n📋 <b>Предпросмотр:</b> <code>"
MSG_DATA_LOST = "⏳ Данные исчезли, контекст потерян.\n\nНажми ещё раз /onehand"

# --- Callback data шаблоны ---
CB_CMD_ONE = f"{OH_PREFIX}cmd_{VAL_ONE}"
CB_CMD_STICKER = f"{OH_PREFIX}cmd_{VAL_STICKER}"
CB_TYPE_CARD = f"{OH_PREFIX}type_{TYPE_CARD}"
CB_TYPE_ORACULUM = f"{OH_PREFIX}type_{TYPE_ORACULUM}"
CB_TYPE_RUNES = f"{OH_PREFIX}type_{TYPE_RUNES}"
CB_TYPE_CANVAS = f"{OH_PREFIX}type_{TYPE_CANVAS}"
CB_CFG_COUNT = f"{OH_PREFIX}cfg_{PARAM_COUNT}"
CB_CFG_DECK = f"{OH_PREFIX}cfg_{PARAM_DECK}"
CB_CFG_MAJOR = f"{OH_PREFIX}cfg_{PARAM_MAJOR}"
CB_CFG_FLIP = f"{OH_PREFIX}cfg_{PARAM_FLIP}"
CB_CFG_GO = f"{OH_PREFIX}cfg_{VAL_GO}"
CB_RUNES_1 = f"{OH_PREFIX}runes_1"
CB_RUNES_3 = f"{OH_PREFIX}runes_3"
CB_RUNES_GO = f"{OH_PREFIX}runes_{VAL_GO}"

# ═══════════════════════════════════════════════════════════════

# Инициализируем асинхронный клиент
redis_client = aioredis.StrictRedis(
    host=os.getenv("REDIS_HOST", "localhost"), 
    port=int(os.getenv("REDIS_PORT", 6379)), 
    db=3,
    decode_responses=True
)
redis_client_bot = aioredis.StrictRedis(
    host=os.getenv("REDIS_HOST", "localhost"), 
    port=int(os.getenv("REDIS_PORT", 6379)), 
    db=2,
    decode_responses=True
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
        """Точка входа: /oh — главная клавиатура."""
        keyboard = [
            [
                InlineKeyboardButton(BTN_ONE_CARD, callback_data=CB_CMD_ONE),
                InlineKeyboardButton(BTN_STICKER, callback_data=CB_CMD_STICKER)
            ],
            [
                InlineKeyboardButton(BTN_TARO, callback_data=CB_TYPE_CARD),
                InlineKeyboardButton(BTN_ORACULUM, callback_data=CB_TYPE_ORACULUM),
            ],
            [
                InlineKeyboardButton(BTN_RUNES, callback_data=CB_TYPE_RUNES),
                InlineKeyboardButton(BTN_CANVAS, callback_data=CB_TYPE_CANVAS),
            ],
        ]
        await update.message.reply_text(
            MSG_CHOOSE_TYPE, reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def _data_lost(self, query):
        """Показывает сообщение об исчезновении данных и убирает клавиатуру."""
        await query.edit_message_text(
            MSG_DATA_LOST,
            reply_markup=None,
            parse_mode=ParseMode.HTML,
        )

    async def handle_onehand_callback(self, update: Update, context: CallbackContext):
        query = update.callback_query
        await query.answer()
        data = query.data.split("_")  # oh_этап_значение_доп
        action = data[1]

        # --- Быстрые команды (без параметров) ---
        if action == "cmd":
            cmd = data[2]
            if cmd == VAL_ONE:
                final_cmd = CMD_ONE
            elif cmd == VAL_STICKER:
                final_cmd = CMD_TAROT
            else:
                return await query.edit_message_text(MSG_UNKNOWN_CMD)

            await self._send_final_command(query, final_cmd)
            return

        # --- Выбор типа (Таро/Оракул/Руны/Холст) ---
        if action == "type":
            gtype = data[2]
            context.user_data[OH_CMD] = {"type": gtype, "params": {}}

            if gtype in [TYPE_CARD, TYPE_ORACULUM, TYPE_CANVAS]:
                return await self._oh_build_config_keyboard(query, context)
            elif gtype == TYPE_RUNES:
                return await self._oh_ask_runes_mode(query, context)
            return

        # --- Изменение параметров в единой клавиатуре ---
        elif action == "cfg":
            # Проверяем, есть ли данные
            if OH_CMD not in context.user_data:
                return await self._data_lost(query)

            param = data[2]
            value = data[3] if len(data) > 3 else None

            if param == PARAM_COUNT:
                context.user_data[OH_CMD]["params"][PARAM_COUNT] = value
            elif param == PARAM_DECK:
                if value == VAL_RANDOM:
                    context.user_data[OH_CMD]["params"].pop(PARAM_DECK, None)
                else:
                    context.user_data[OH_CMD]["params"][PARAM_DECK] = f"deck_{value}"
            elif param == PARAM_MAJOR:
                if value == VAL_YES:
                    context.user_data[OH_CMD]["params"][PARAM_MAJOR] = DECK_MAJOR
                else:
                    context.user_data[OH_CMD]["params"].pop(PARAM_MAJOR, None)
            elif param == PARAM_FLIP:
                if value == VAL_YES:
                    context.user_data[OH_CMD]["params"][PARAM_FLIP] = DECK_FLIP
                else:
                    context.user_data[OH_CMD]["params"].pop(PARAM_FLIP, None)
            elif param == VAL_GO:
                return await self._oh_finish(query, context)

            return await self._oh_build_config_keyboard(query, context)

        # --- Руны: переключатель 1/3 + получить команду ---
        elif action == "runes":
            # Проверяем, есть ли данные
            if OH_CMD not in context.user_data:
                return await self._data_lost(query)

            sub = data[2]

            if sub in ["1", "3"]:
                context.user_data[OH_CMD]["params"][PARAM_COUNT] = sub
                return await self._oh_ask_runes_mode(query, context)

            elif sub == VAL_GO:
                return await self._oh_finish(query, context)

    # --- Единая клавиатура настройки ---

    def _build_preview_command(self, gtype: str, params: Dict) -> str:
        """Собирает превью команды по текущим параметрам."""
        if gtype == TYPE_RUNES:
            count = params.get(PARAM_COUNT, "1")
            return CMD_FUTHARK_TRIPLET if count == "3" else CMD_FUTHARK

        CMD_PREFIX_MAP = {
            TYPE_CARD: "card",
            TYPE_ORACULUM: "oraculum",
            TYPE_CANVAS: "canvas",
        }
        prefix = CMD_PREFIX_MAP.get(gtype, gtype)
        count = params.get(PARAM_COUNT, "1")
        
        if count == '1':
            base = f"/{prefix}"
        else:
            base = f"/{prefix}{count}"

        extra = []
        if PARAM_DECK in params and params[PARAM_DECK]:
            extra.append(params[PARAM_DECK])
        if PARAM_FLIP in params:
            extra.append(params[PARAM_FLIP])
        if PARAM_MAJOR in params:
            extra.append(params[PARAM_MAJOR])

        if extra:
            base += "_" + "_".join(extra)
        return base

    async def _oh_build_config_keyboard(self, query, context):
        """
        Единая клавиатура конфигурации:
        [1 3 6 9]
        [случайно любимая райдер уэйт]
        [мажор не мажор]
        [флип не флип]
        [получить команду]
        """
        # Проверяем, есть ли данные
        if OH_CMD not in context.user_data:
            return await self._data_lost(query)

        cmd_data = context.user_data[OH_CMD]
        gtype = cmd_data["type"]
        params = cmd_data["params"]

        # Текущие значения
        count = params.get(PARAM_COUNT, "1")
        deck = params.get(PARAM_DECK, None)  # None = случайно, DECK_RIDER, DECK_LENORMAND
        major = params.get(PARAM_MAJOR, None)
        flip = params.get(PARAM_FLIP, None)

        # --- Ряд 1: Количество ---
        row1 = [
            InlineKeyboardButton(
                f"{MARK_CHECK if count == str(i) else ''}{i}", 
                callback_data=f"{CB_CFG_COUNT}_{i}"
            )
            for i in [1, 3, 6, 9]
        ]

        # --- Ряд 2: Колоды ---
        if gtype == TYPE_ORACULUM:
            row2 = [
                InlineKeyboardButton(
                    f"{MARK_CHECK if deck is None else ''}{BTN_RANDOM}", 
                    callback_data=f"{CB_CFG_DECK}_{VAL_RANDOM}"
                ),
                InlineKeyboardButton(
                    f"{MARK_CHECK if deck == DECK_LENORMAND else ''}{BTN_LENORMAND}", 
                    callback_data=f"{CB_CFG_DECK}_{VAL_LENORMAND}"
                ),
            ]
        else:
            # card, canvas — таро
            row2 = [
                InlineKeyboardButton(
                    f"{MARK_CHECK if deck is None else ''}{BTN_RANDOM}", 
                    callback_data=f"{CB_CFG_DECK}_{VAL_RANDOM}"
                ),
                InlineKeyboardButton(
                    f"{MARK_CHECK if deck == DECK_RIDER else ''}{BTN_RIDER}", 
                    callback_data=f"{CB_CFG_DECK}_{VAL_RIDER}"
                ),
            ]

        # --- Ряд 3: Старшие арканы (только для таро и холста) ---
        if gtype in [TYPE_CARD, TYPE_CANVAS]:
            row3 = [
                InlineKeyboardButton(
                    f"{MARK_CHECK if major == DECK_MAJOR else ''}{BTN_MAJOR_ONLY}", 
                    callback_data=f"{CB_CFG_MAJOR}_{VAL_YES}"
                ),
                InlineKeyboardButton(
                    f"{MARK_CHECK if major is None else ''}{BTN_ALL_ARCANA}", 
                    callback_data=f"{CB_CFG_MAJOR}_{VAL_NO}"
                ),
            ]
        else:
            row3 = []

        # --- Ряд 4: Перевернутые (для таро, оракула, холста) ---
        if gtype in [TYPE_CARD, TYPE_ORACULUM, TYPE_CANVAS]:
            row4 = [
                InlineKeyboardButton(
                    f"{MARK_CHECK if flip == DECK_FLIP else ''}{BTN_WITH_FLIP}", 
                    callback_data=f"{CB_CFG_FLIP}_{VAL_YES}"
                ),
                InlineKeyboardButton(
                    f"{MARK_CHECK if flip is None else ''}{BTN_NO_FLIP}", 
                    callback_data=f"{CB_CFG_FLIP}_{VAL_NO}"
                ),
            ]
        else:
            row4 = []

        # --- Ряд 5: Получить команду ---
        row5 = [InlineKeyboardButton(BTN_GET_CMD, callback_data=CB_CFG_GO)]

        keyboard = [row1, row2]
        if row3:
            keyboard.append(row3)
        if row4:
            keyboard.append(row4)
        keyboard.append(row5)

        # Формируем текст с текущей конфигурацией
        parts = []
        parts.append(f"{MSG_COUNT_LABEL} {count}")
        if gtype == TYPE_ORACULUM:
            parts.append(f"{MSG_DECK_LABEL_ORACULUM} {'Ленорман' if deck else 'Случайно'}")
        else:
            parts.append(f"{MSG_DECK_LABEL_TARO} {'Райдер-Уэйт' if deck else 'Случайно'}")
        if gtype in [TYPE_CARD, TYPE_CANVAS]:
            parts.append(f"{MSG_ARCANA_LABEL} {MSG_MAJOR_ONLY_TXT if major else MSG_ALL_ARCANA_TXT}")
        parts.append(f"{MSG_FLIP_LABEL} {MSG_FLIP_YES if flip else MSG_FLIP_NO}")

        # Добавляем превью команды
        preview = self._build_preview_command(gtype, params)
        parts.append(f"{MSG_PREVIEW_PREFIX}{preview}</code>")

        text = MSG_CONFIG_TITLE + "\n".join(parts)

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML,
        )

    async def _oh_ask_runes_mode(self, query, context):
        """Переключалка для рун: 1 руна / 3 руны + кнопка получить команду."""
        # Проверяем, есть ли данные
        if OH_CMD not in context.user_data:
            return await self._data_lost(query)

        params = context.user_data[OH_CMD]["params"]
        count = params.get(PARAM_COUNT, "1")

        # Добавляем превью команды для рун
        preview = self._build_preview_command(TYPE_RUNES, params)
        text = f"{MSG_CHOOSE_RUNES}\n{MSG_PREVIEW_PREFIX}{preview}</code>"

        kb = [
            [
                InlineKeyboardButton(
                    f"{MARK_CHECK if count == '1' else ''}{BTN_1_RUNE}", 
                    callback_data=CB_RUNES_1
                ),
                InlineKeyboardButton(
                    f"{MARK_CHECK if count == '3' else ''}{BTN_3_RUNES}", 
                    callback_data=CB_RUNES_3
                ),
            ],
            [InlineKeyboardButton(BTN_GET_CMD, callback_data=CB_RUNES_GO)],
        ]
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML,
        )

    async def _oh_finish(self, query, context):
        """Финальная сборка команды."""
        # Проверяем, есть ли данные
        if OH_CMD not in context.user_data:
            return await self._data_lost(query)

        cmd_data = context.user_data.get(OH_CMD, {})
        gtype = cmd_data.get("type", "")
        params = cmd_data.get("params", {})

        # --- Руны ---
        if gtype == TYPE_RUNES:
            count = params.get(PARAM_COUNT, "1")
            if count == "3":
                final_cmd = CMD_FUTHARK_TRIPLET
            else:
                final_cmd = CMD_FUTHARK

        # --- Таро / Оракул / Холст ---
        else:
            count = params.get(PARAM_COUNT, "1")

            CMD_PREFIX_MAP = {
                TYPE_CARD: "card",
                TYPE_ORACULUM: "oraculum",
                TYPE_CANVAS: "canvas",
            }
            prefix = CMD_PREFIX_MAP.get(gtype, gtype)
            base = f"/{prefix}{count}"
            if count == '1':
                base = f"/{prefix}"

            extra = []
            if PARAM_DECK in params and params[PARAM_DECK]:
                extra.append(params[PARAM_DECK])
            if PARAM_FLIP in params:
                extra.append(params[PARAM_FLIP])
            if PARAM_MAJOR in params:
                extra.append(params[PARAM_MAJOR])

            final_cmd = base
            if extra:
                final_cmd += "_" + "_".join(extra)

        context.user_data.pop(OH_CMD, None)
        await self._send_final_command(query, final_cmd)

    async def _send_final_command(self, query, final_cmd):
        """Отправляет финальную команду пользователю."""
        await query.delete_message()

        reply_keyboard = ReplyKeyboardMarkup(
            [[final_cmd]], one_time_keyboard=True, resize_keyboard=True
        )

        await query.message.reply_text(
            f"{MSG_CMD_READY_PREFIX}{final_cmd}{MSG_CMD_READY_SUFFIX}",
            reply_markup=reply_keyboard,
            parse_mode=ParseMode.HTML,
        )