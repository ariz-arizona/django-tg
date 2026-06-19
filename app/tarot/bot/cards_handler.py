import re
import os
from typing import List, Optional, Dict
from collections import Counter

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
    OraculumDeck,
    OraculumItem,
    UserReading,
)
from tg_bot.models import BotFileCache
from server.logger import logger
from django.conf import settings

from tarot.messages import CardMessages, TAROT_3_TRIGGER

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

class CardsHandler:
    """Обработчик команды /all и связанных callback'ов."""

    def __init__(self, bot_instance):
        """
        Инициализация обработчика.

        Args:
            bot_instance: Экземпляр основного бота для доступа к его методам и атрибутам
        """
        self.bot = bot_instance
        self.messages = CardMessages()
    
    @property
    def app_bot_id(self):
        """Получаем app_bot_id из основного бота."""
        return self.bot.app_bot_id

    def get_handlers(self):
        """Возвращает список обработчиков для этой команды."""
        return [
            MessageHandler(
                filters.Text([TAROT_3_TRIGGER]) & filters.ChatType.PRIVATE,
                self.handle_card
            ),
            MessageHandler(
                filters.COMMAND
                & filters.TEXT
                & filters.ChatType.PRIVATE
                & filters.Regex(r"^\/card(\d+)?"),
                self.handle_card,
            ),
            CallbackQueryHandler(self.handle_more_button, pattern=r"^more_"),
            
            MessageHandler(
                filters.COMMAND
                & filters.TEXT
                & filters.ChatType.PRIVATE
                & filters.Regex(r"^\/oraculum(\d+)?"),
                self.handle_oraculum,
            ),
            CallbackQueryHandler(
                self.handle_moreoracle_button, pattern=r"^moreoracle_"
            ),
        ]
    
    
    async def handle_card(self, update: Update, context: CallbackContext):
        """
        Обработчик команды /card.
        """
        msg_text = update.message.text
        logger.info(f"Обработка команды /card с текстом: {msg_text[:100]}")

        category = UserReading.ReadingCategory.TAROT
        if await self.bot.check_reading_cooldown(update, category):
            return

        try:
            status_message = await update.message.reply_text(
                self.messages.get_loading(), parse_mode=ParseMode.HTML
            )

            if msg_text == TAROT_3_TRIGGER:
                options = {
                    "counter": 3,
                    "deck": None,
                    "flip": True,          # Обязательно, иначе упадет проверка на flip
                    "major": False,         # Ваш запрос
                    "card_ids": None,       # Указываем явно, что кастомных ID нет
                    "original_query": ""    # Пустая строка для корректного логгера
                }                
            else:
                # Стандартный парсинг для команды /card
                options = self.bot.parse_reading_options(msg_text)
            logger.info(f"Опции расклада разобраны: {options}")

            # 1. Получение колоды
            deck = await self.bot.get_deck(options.get("deck"))
            logger.info(f"Используемая колода ID: {deck.id if deck else 'None'}")

            # 2. Генерация карт
            cards = await self.bot.get_cards(
                deck_id=deck.id if deck else None,
                counter=options.get("counter", 1),
                card_ids=options.get("card_ids"),
                major=options.get("major", False),
                flip=options.get('flip')
            )
            
            # Формируем список словарей для БД
            card_records = [{"id": str(c["card_id"]), "flip": c["flipped"]} for c in cards]
            logger.info(f"Получены карты {card_records}")
            
            # 3. Сохранение расклада
            user = await self.bot.get_or_create_tg_user(update)
            reading = await self.bot.save_reading(
                user=user,
                message_id=update.effective_message.message_id,
                text=f"{deck.name if deck else 'Дефолтная колода'}: " + 
                     ", ".join([await self.bot.format_card_name(c) for c in cards]),
                category=category,
                count=options.get("counter", 1),
                deck_id=deck.id if deck else None,
                is_flipped_allowed=options.get('flip', False),
                is_major_only=options.get('major', False),
                card_ids=card_records,
                original_query=options.get('original_query'),
            )
            logger.info(f"Результат гадания сохранен в БД, ID записи: {reading.id}")

            # 4. Подготовка клавиатуры и отправка
            send_card_kwargs = {
                "reading_id": reading.id,
                "send_type": "tarot",
            }
            
            if await self.bot.ai_interpret_handler.should_add_ai_button():
                send_card_kwargs["add_ai_button"] = "🔮 Растолковать расклад (ИИ)"
                logger.info("ИИ-кнопка добавлена в параметры отправки.")

            # Отправка карт пользователю
            await self.send_card(
                update,
                cards,
                **send_card_kwargs
            )
            
            # Логируем результат
            card_names_info = [f"{c['name']} {c['card_id']} ({'Flipped' if c['flipped'] else 'Direct'})" for c in cards]
            logger.info(f"Карты успешно отправлены: {card_names_info}")

            # Удаляем статусное сообщение
            await status_message.delete()

        except Exception as e:
            logger.error(f"Ошибка при обработке команды /card: {e}", exc_info=True)
            await update.message.reply_text(
                self.messages.get_error_message("generic", error_details=str(e))
            )

    async def handle_more_button(self, update: Update, context: CallbackContext):
        query = update.callback_query
        await query.answer()
        logger.info(f"Получен callback-запрос: {query.data}")

        try:
            _, reading_id = query.data.split("_")
            user = await self.bot.get_or_create_tg_user(update)

            # Ищем существующий расклад
            reading = await UserReading.objects.filter(id=reading_id, user=user).afirst()

            # УСЛОВИЕ: Если расклада нет — чистим кнопки и выходим
            if not reading:
                logger.warning(f"Расклад {reading_id} не найден.")
                await query.edit_message_text(
                    self.messages.get_error_message("no_cards")
                )
                await query.edit_message_reply_markup(reply_markup=None)
                return

            # === Подготовка данных ===
            exclude_cards = [str(item.get("id") if isinstance(item, dict) else item) for item in (reading.card_ids or [])]
            logger.info(f"Получаем карты с major {bool(reading.is_major_only)} flip {bool(reading.is_flipped_allowed)}. Исключаем: {exclude_cards}")

            new_card = await self.bot.get_cards(
                deck_id=reading.deck_id,
                counter=1,
                exclude_cards=exclude_cards,
                major=bool(reading.is_major_only),
                flip=bool(reading.is_flipped_allowed)
            )

            if not new_card:
                await query.edit_message_text(
                    self.messages.get_error_message("no_cards")
                )
                await query.edit_message_reply_markup(reply_markup=None)
                return

            # === Обновление записи ===
            new_card_text = ", ".join([await self.bot.format_card_name(c) for c in new_card])
            new_card_data = [{"id": c["card_id"], "flip": c["flipped"]} for c in new_card]
            logger.info(f"Выбраны новые карты: {[c['name'] + ' ' + str(c['flipped']) for c in new_card]}")

            reading.text = f"{reading.text}, {new_card_text}"
            reading.count += 1
            reading.card_ids.extend(new_card_data)
            await reading.asave()

            # === Отправка результата ===
            send_card_kwargs = {"reading_id": reading.id, "send_type": "tarot"}
            if await self.bot.ai_interpret_handler.should_add_ai_button():
                send_card_kwargs["add_ai_button"] = "🔮 Растолковать расклад (ИИ)"

            await self.send_card(update, new_card, **send_card_kwargs)

            # Очистка кнопок у старого сообщения
            await query.edit_message_reply_markup(reply_markup=None)

        except Exception as e:
            logger.error(f"Ошибка при обработке добора карты: {e}", exc_info=True)
            await query.edit_message_text(
                self.messages.get_error_message("generic", error_details=str(e))
            )

    async def handle_oraculum(self, update: Update, context: CallbackContext):
        msg_text = update.message.text
        logger.info(f"Обработка команды /oraculum: {msg_text[:100]}")

        category = UserReading.ReadingCategory.ORACLE
        if await self.bot.check_reading_cooldown(update, category):
            return

        try:
            status_message = await update.message.reply_text(
                self.messages.get_loading(), parse_mode=ParseMode.HTML,
            )

            options = self.bot.parse_reading_options(msg_text)
            
            # 1. Получение колоды
            deck = await self.bot.get_deck(options.get("deck"), "oraculum")
            
            # 2. Получение карт
            cards = await self.bot.get_oraculum_cards(
                deck.id if deck else None, 
                options.get("counter", 1), 
                [],
                options.get('flip', False)
            )
            
            # 3. Сохранение расклада (используем консистентный формат card_ids)
            user = await self.bot.get_or_create_tg_user(update)
            # Для оракула тоже используем формат списка словарей для единства БД
            card_records = [{"id": str(c["card_id"]), "flip": c["flipped"]} for c in cards]
            
            reading = await self.bot.save_reading(
                user=user,
                message_id=update.effective_message.message_id,
                text=f"{deck.name if deck else 'Дефолтный оракул'}: " + 
                     ", ".join([await self.bot.format_card_name(c) for c in cards]),
                category=category,
                count=options.get("counter", 1),
                deck_id=deck.id if deck else None,
                is_flipped_allowed=options.get('flip', False),
                is_major_only=False,
                card_ids=card_records
            )
            logger.info(f"Результат оракула сохранен в БД, ID: {reading.id}")

            # 4. Отправка карт через обновленный send_card
            send_card_kwargs = {
                "reading_id": reading.id,
                "send_type": "oracle", # Новый тип отправки
            }

            await self.send_card(
                update,
                cards,
                **send_card_kwargs
            )
            
            logger.info(f"Карты оракула успешно отправлены: {[c['name'] for c in cards]}")

            # Удаляем статусное сообщение
            await status_message.delete()

        except Exception as e:
            logger.error(f"Ошибка при обработке команды /oraculum: {e}", exc_info=True)
            await update.message.reply_text(
                self.messages.get_error_message("generic", error_details=str(e))
            )
            
    async def handle_moreoracle_button(self, update: Update, context: CallbackContext):
        query = update.callback_query
        await query.answer()
        logger.info(f"Получен callback-запрос: {query.data}")
        
        try:
            # Получаем reading_id напрямую из callback_data
            _, reading_id = query.data.split("_")
            
            # 1. Поиск расклада по ID
            reading = await UserReading.objects.filter(id=reading_id).afirst()
            
            if not reading:
                logger.warning(f"Расклад с ID {reading_id} не найден.")
                await query.edit_message_text(
                    self.messages.get_error_message("no_cards")
                )
                return
            
            # 2. Подготовка исключений (извлекаем card_ids из БД)
            exclude_cards = [
                str(item.get("id") if isinstance(item, dict) else item) 
                for item in (reading.card_ids or [])
            ]
            logger.info(f"Получаем карты оракула с major {bool(reading.is_major_only)} flip {bool(reading.is_flipped_allowed)}. Исключаем: {exclude_cards}")

            # 3. Генерация карты (используем настройки из самого расклада)
            new_card = await self.bot.get_oraculum_cards(
                deck_id=reading.deck_id,
                counter=1,
                exclude_cards=exclude_cards,
                flip=reading.is_flipped_allowed
            )

            if not new_card:
                await query.edit_message_text(
                    self.messages.get_error_message("no_cards")
                )
                return

            # 4. Обновление БД
            new_card_data = [{"id": str(c["card_id"]), "flip": c["flipped"]} for c in new_card]
            new_card_text = ", ".join([await self.bot.format_card_name(c) for c in new_card])
            logger.info(f"Выбраны новые карты: {[c['name'] + ' ' + str(c['flipped']) for c in new_card]}")
            
            reading.text = f"{reading.text}, {new_card_text}"
            reading.count += 1
            reading.card_ids.extend(new_card_data)
            await reading.asave()

            # 5. Отправка карты через унифицированный send_card
            await self.send_card(
                update,
                new_card,
                send_type="oracle",
                reading_id=reading.id
            )
            
            await query.edit_message_reply_markup(reply_markup=None)
            logger.info(f"Карта добора Оракула #{reading_id} успешно отправлена.")

        except Exception as e:
            logger.error(f"Ошибка при доборе карты Оракула: {e}", exc_info=True)
            await query.edit_message_text(
                self.messages.get_error_message("generic", error_details=str(e)),
                parse_mode=ParseMode.HTML,
            )

    async def send_card(self, update: Update, cards, **kwargs):
        reading_id = kwargs.get("reading_id")
        send_type = kwargs.get("send_type") # 'tarot' или 'oracle'
        params = {"disable_web_page_preview": True}

        # 1. Отправка фото
        await update.effective_message.reply_media_group(
            [InputMediaPhoto(c["img_id"], await self.bot.format_card_name(c)) for c in cards],
            reply_to_message_id=update.effective_message.message_id,
        )

        reply_markup = []
        text = []
        
        def local_format_card_name(card_name: str, is_flipped: bool) -> str:
            clean_name = self.messages.clean_card_name(card_name)
            if is_flipped:
                return f"{clean_name} ⬇️"
            return clean_name

        # 2. Логика для ТАРО
        if send_type == 'tarot':
            reading = await UserReading.objects.aget(id=reading_id)
            current_deck = await TarotDeck.objects.aget(id=reading.deck_id)
            
            order_list = [str(item.get("id")) for item in (reading.card_ids or [])]
            flip_map = {str(item.get("id")): item.get("flip", False) for item in (reading.card_ids or [])}
            
            all_cards_qs = TarotCardItem.objects.filter(
                deck_id=current_deck.id, tarot_card__card_id__in=order_list
            ).prefetch_related("tarot_card")
            
            cards_dict = {}
            async for c in all_cards_qs:
                card_id_str = str(c.tarot_card.card_id)
                cards_dict[card_id_str] = {
                    "card_instance": c,
                    "name": c.tarot_card.name,
                    "flipped": flip_map.get(card_id_str, False)
                }
            
            all_cards = [cards_dict[cid] for cid in order_list if cid in cards_dict]
            
            total_query = TarotCardItem.objects.filter(deck_id=current_deck.id)
            if reading.is_major_only:
                total_query = total_query.filter(tarot_card__is_major=True)
            total_cards = await total_query.acount()
            can_draw_query = total_query.exclude(tarot_card__card_id__in=order_list)
            can_draw = await can_draw_query.aexists()
            current_count = len(all_cards)

            # Формируем описание карт как список строк
            cards_description = [local_format_card_name(c['name'], c['flipped']) for c in all_cards]
            
            # Статистика по колоде
            stats_str = self.messages.get_deck_stats(current_count, total_cards)
            
            # Текст для больших раскладов
            try_all_str = None
            if current_count > 10:
                flag = "_flip" if reading.is_flipped_allowed else ""
                try_all_str = self.messages.get_try_all_deck(
                    deck_id=current_deck.id,
                    flip_flag=flag
                )
                
            text = [self.messages.format_description(
                deck_name=current_deck.name,
                cards_description=cards_description,
                stats_str=stats_str,
                try_all_str=try_all_str
            )]
                
            # Проверка на любимую команду - добавляем отдельной строкой
            last_readings = [r async for r in UserReading.objects.filter(user_id=reading.user_id).order_by('-created_at')[:3]]
            current = last_readings[0]
            if (
                current.count > 1
                and len(last_readings) >= 3
                and all(r.deck_id == current.deck_id for r in last_readings)
            ):
                favorite_cmd = f"/card{current.count}_deck_{current.deck_id}"
                favorite_text = self.messages.get_favorite_command(command=favorite_cmd)
                # Добавляем любимую команду в конец текста
                text.append(f"\n{favorite_text}")
                
            params["parse_mode"] = ParseMode.HTML
            
            row = [InlineKeyboardButton("Еще карту", callback_data=f"more_{reading_id}")] if can_draw else []
            row.append(InlineKeyboardButton(f"Трактовка карт ({len(all_cards)})", callback_data=f"desc_{reading_id}"))
            reply_markup.append(row)
            if ai_btn := kwargs.get("add_ai_button"):
                reply_markup.append([InlineKeyboardButton(text=ai_btn, callback_data=f"aireading_{reading_id}")])

        # 3. Логика для ОРАКУЛА
        elif send_type == 'oracle':
            reading = await UserReading.objects.aget(id=reading_id)
            current_deck = await OraculumDeck.objects.aget(id=reading.deck_id)
            
            order_list = [str(item.get("id")) for item in (reading.card_ids or [])]
            flip_map = {str(item.get("id")): item.get("flip", False) for item in (reading.card_ids or [])}
            
            all_cards_qs = OraculumItem.objects.filter(
                deck_id=current_deck.id, 
                id__in=order_list
            )
            
            cards_dict = {str(c.id): c async for c in all_cards_qs}
            all_cards = []
            for cid in order_list:
                if cid in cards_dict:
                    card_obj = cards_dict[cid]
                    is_flipped = flip_map.get(cid, False)
                    all_cards.append({
                        "name": card_obj.name,
                        "flipped": is_flipped
                    })
                    
            total_cards = await OraculumItem.objects.filter(deck_id=current_deck.id).acount()
            current_count = len(all_cards)
            
            card_names = [local_format_card_name(c['name'], c['flipped']) for c in all_cards]
            stats_str = self.messages.get_deck_stats(current_count, total_cards)
            
            text = [self.messages.format_description(
                deck_name=current_deck.name,
                deck_description=current_deck.description, 
                cards_description=card_names,
                stats_str=stats_str
            )]
            
            params["parse_mode"] = ParseMode.HTML
            
            if current_count < total_cards:
                reply_markup.append([InlineKeyboardButton("Еще карту", callback_data=f"moreoracle_{reading_id}")])

        # 4. Финальная отправка
        params["text"] = "\n".join(text)
        
        reply_target = update.effective_message.reply_to_message
        params["reply_to_message_id"] = (
            reply_target.message_id if reply_target else update.effective_message.message_id
        )
        
        if reply_markup:
            params["reply_markup"] = InlineKeyboardMarkup(reply_markup)

        await update.effective_message.reply_text(**params)