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
)
from tg_bot.models import BotFileCache
from server.logger import logger
from django.conf import settings

from tarot.utils.image_utils import create_spread_image
from tarot.bot.allcard_handler import AllCardHandler
from tarot.bot.ai_interpret_handler import AIInterpretHandler
from tarot.bot.rune_handler import RuneHandler

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

class TarotBot(AbstractBot):
    def __init__(self):
        self.allcard_handler = AllCardHandler(self)
        self.ai_interpret_handler = AIInterpretHandler(self)
        self.rune_handler = RuneHandler(self)
        self.handlers = self.get_handlers()

    def get_handlers(self):
        return [
            MessageHandler(filters.PHOTO, self.handle_photo_msg),
            CommandHandler("start", self.handle_help),
            CommandHandler("help", self.handle_help),
            
            *self.allcard_handler.get_handlers(),
            *self.rune_handler.get_handlers(),
            *self.ai_interpret_handler.get_handlers(),
            
            MessageHandler(
                filters.COMMAND
                & filters.TEXT
                & filters.ChatType.PRIVATE
                & filters.Regex(r"^\/decks( oraculum)?$"),
                self.handle_decks,
            ),
            CallbackQueryHandler(
                self.handle_decks_page,
                pattern=r"^deckspage_\d+_(oraculum|tarot)$",
            ),
            CommandHandler("last", self.handle_last_readings, filters.ChatType.PRIVATE),
            
            CommandHandler("one", self.handle_one_command, filters.ChatType.PRIVATE),
            MessageHandler(
                filters.COMMAND
                & filters.TEXT
                & filters.ChatType.PRIVATE
                & filters.Regex(r"^\/card(\d+)?"),
                self.handle_card,
            ),
            CallbackQueryHandler(self.handle_more_button, pattern=r"^more_"),
            CallbackQueryHandler(
                self.handle_desc_button, pattern=r"^desc_(\d+(?:#\d+)*)$"
            ),
            CallbackQueryHandler(
                self.handle_pagination, pattern=r"^meaning_"
            ),
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
            MessageHandler(
                filters.COMMAND
                & filters.TEXT
                & filters.ChatType.PRIVATE
                & filters.Regex(r"^\/(spread|canvas)"),
                self.handle_spread,
            ),
        ]

    async def get_or_create_tg_user(self, update: Update) -> TgUser:
        """
        Получает или создает пользователя TgUser на основе данных из Telegram Update.
        """
        tg_user = update.effective_user
        if not tg_user:
            return None

        user_obj, _ = await TgUser.objects.aget_or_create(
            tg_id=tg_user.id,
            defaults={
                "username": tg_user.username,
                "first_name": tg_user.first_name,
                "last_name": tg_user.last_name,
                "language_code": tg_user.language_code,
                "is_bot": tg_user.is_bot,
            },
        )
        return user_obj

    async def save_reading(
        self, 
        user: TgUser, 
        message_id: int, 
        text: str, 
        category: str = "tarot", 
        count: int = 1,
        deck_id: int = None, 
        is_flipped_allowed: bool = False, 
        is_major_only: bool = False,
        card_ids: list = None,
        **kwargs,
    ):
        # 1. Защита от пустых значений для JSONField
        if card_ids is None:
            card_ids = []
        original_query = kwargs.pop("original_query", "")

        # 2. Создаем запись в новой типизированной модели UserReading
        reading = await UserReading.objects.acreate(
            bot_id=self.app_bot_id,
            user=user,
            category=category,
            count=count,
            deck_id=deck_id,
            is_flipped_allowed=is_flipped_allowed,
            is_major_only=is_major_only,
            text=text,
            message_id=message_id,
            card_ids=card_ids, 
            original_query=original_query,
        )
        logger.info(f"Результат гадания сохранен: {reading}")

        # 3. Сохраняем отметку в Redis
        try:
            # Формируем ключ, например: "user:123456789:tarot"
            redis_key = REDIS_KEY_TEMPLATE.format(user_id=user.tg_id, category=category)
            await redis_client.set(redis_key, reading.id, ex=REDIS_TTL_SECONDS) 
            logger.info(f"Ключ {redis_key} успешно записан в Redis на {REDIS_TTL_SECONDS} сек.")
        except Exception as e:
            logger.error(f"Ошибка записи в Redis для пользователя {user.id}: {e}")

        return reading

    async def get_deck(self, deck_id=None, deck_type="tarot"):
        # Получаем список всех ID колод
        model = TarotDeck.objects
        if deck_type == "oraculum":
            model = OraculumDeck.objects
        deck_ids: List[int] = [deck.id async for deck in model.all()]
        logger.info(f"Получаем колоду с ID {deck_id}")

        if not deck_ids:
            raise ValueError("Нет доступных колод.")

        if deck_id is not None and deck_id not in deck_ids:
            logger.error("Указанный ID колоды не существует.")
            deck_id = None

        if deck_id is None:
            deck_id = random.choice(deck_ids)

        # Получаем колоду по ID
        try:
            return await model.aget(id=deck_id)
        except Exception:
            # На случай, если колода была удалена между получением списка ID и запросом
            raise ValueError("Не удалось получить колоду.")

    async def get_cards(
        self,
        deck_id: int,
        counter: int = 1,
        card_ids: Optional[List[str]] = None,  # Используем card_id (str)
        major: bool = False,
        flip: bool = False,
        exclude_cards: Optional[List[str]] = None,
    ) -> List[dict]:
        try:
            if card_ids is None:
                card_ids = []

            # 1. Проверка существования колоды
            if not await TarotDeck.objects.filter(id=deck_id).aexists():
                raise ValueError(f"Колода {deck_id} не найдена")
            # 2. Базовый запрос карт колоды
            filters = {"deck_id": deck_id}
            if major and not len(card_ids):
                filters["tarot_card__is_major"] = major

            base_query = TarotCardItem.objects.filter(**filters).prefetch_related(
                "tarot_card", "files"
            )

            # 3. Обработка ручного выбора карт
            manual_cards = []
            if card_ids:
                card_ids = [str(cid) for cid in card_ids]
                unique_ids = list(dict.fromkeys(card_ids))

                existing_cards = [
                    card
                    async for card in base_query.filter(
                        tarot_card__card_id__in=card_ids
                    )
                ]

                id_to_card = {card.tarot_card.card_id: card for card in existing_cards}
                manual_cards = [
                    id_to_card[cid] for cid in unique_ids if cid in id_to_card
                ]

            # 4. Получаем все доступные card_id
            all_card_ids = [
                card_id
                async for card_id in base_query.values_list(
                    "tarot_card__card_id", flat=True
                )
            ]

            if exclude_cards:
                exclude_cards = [
                    str(cid) for cid in exclude_cards
                ]  # Преобразуем в строки
                all_card_ids = [cid for cid in all_card_ids if cid not in exclude_cards]

            # 5. Вычисляем оставшиеся карты
            remaining = max(0, counter - len(manual_cards))
            exclude_ids = {card.tarot_card.card_id for card in manual_cards}
            available_ids = [cid for cid in all_card_ids if cid not in exclude_ids]

            if len(available_ids) < remaining:
                return []

            # 6. Случайная выборка через Python
            random_ids = random.sample(available_ids, remaining) if remaining else []

            # 7. Получаем случайные карты
            random_cards = [
                await base_query.aget(tarot_card__card_id=cid) for cid in random_ids
            ]

            # 8. Формируем результат
            combined = manual_cards + random_cards

            result = []

            # Обычный цикл for, который отлично работает с await
            for card in combined[:counter]:
                # Получаем img_id через ваш асинхронный метод
                img_id = await card.aget_file_id(self.app_bot_id)

                result.append({
                    "card_instance": card,
                    "card_id": card.tarot_card.card_id,
                    "img_id": img_id,
                    "name": card.tarot_card.name,
                    "flipped": random.choice([True, False]) if flip else False,
                })
            logger.info(f"Получено карт: {len(result)}")
            return result

        except ObjectDoesNotExist as e:
            raise ValueError("Карта не найдена") from e
        except Exception as e:
            raise RuntimeError(f"Ошибка: {str(e)}") from e

    async def get_oraculum_cards(self, deck_id, counter, exclude_cards, flip):
        try:
            # 1. Проверка существования колоды
            if not await OraculumDeck.objects.filter(id=deck_id).aexists():
                raise ValueError(f"Колода {deck_id} не найдена")

            # 2. Базовый запрос карт колоды
            base_query = OraculumItem.objects.filter(deck_id=deck_id).prefetch_related("files")

            # 3. Исключение указанных карт
            if exclude_cards:
                base_query = base_query.exclude(id__in=exclude_cards)

            # 4. Получаем все доступные ID карт
            all_card_ids: List[int] = [
                card_id async for card_id in base_query.values_list("id", flat=True)
            ]

            if len(all_card_ids) < counter:
                return []

            # 5. Выборка случайных ID карт
            random_ids: List[int] = random.sample(all_card_ids, counter)

            # 6. Получение карт по выбранным ID
            random_cards: List[OraculumItem] = [
                await base_query.aget(id=cid) for cid in random_ids
            ]

            result = []

            for card in random_cards:
                # Используем тот же асинхронный метод из миксина
                img_id = await card.aget_file_id(self.app_bot_id)

                result.append({
                    "card_instance": card,
                    "card_id": card.id,
                    "img_id": img_id,
                    "name": card.name,
                    "flipped": random.choice([True, False]) if flip else False,
                })

            return result

        except ObjectDoesNotExist as e:
            raise ValueError("Карта не найдена") from e
        except Exception as e:
            raise RuntimeError(f"Ошибка: {str(e)}") from e

    async def format_card_name(self, card, text_join = '\n'):
        instance = card.get("card_instance")
        flipped = card.get("flipped", False)

        # Основное описание (название или описание из модели)
        main_desc = ""
        if isinstance(instance, OraculumItem):
            # Если description пустой, берем name
            main_desc = (instance.description or "")

        # Текст значения (прямое или перевернутое)
        value_text = ""
        if isinstance(instance, OraculumItem):
            if flipped and instance.inverted:
                value_text = f"Перевернуто: {instance.inverted}"
            else:
                value_text = instance.direct or ""

        # Собираем все части
        parts = [
            card.get("name"),
            "Перевернуто" if flipped else None,
            (f"{main_desc} {value_text}".strip() if isinstance(instance, OraculumItem) else None)
        ]

        return text_join.join(str(p) for p in parts if p)
    
    async def send_card(self, update: Update, cards, **kwargs):
        reading_id = kwargs.get("reading_id")
        send_type = kwargs.get("send_type") # 'tarot' или 'oracle'
        params = {"disable_web_page_preview": True}

        # 1. Отправка фото
        await update.effective_message.reply_media_group(
            [InputMediaPhoto(c["img_id"], await self.format_card_name(c)) for c in cards],
            reply_to_message_id=update.effective_message.message_id,
        )

        reply_markup = []
        text = []
        
        def format_card_name(card_name: str, is_flipped: bool) -> str:
            """
            Форматирует имя карты оракула с учетом переворота.
            Пример: "Младенец ⬇️" или "Младенец"
            """
            if is_flipped:
                return f"{card_name} ⬇️"
            return card_name

        # 2. Логика для ТАРО
        if send_type == 'tarot':
            reading = await UserReading.objects.aget(id=reading_id)
            current_deck = await TarotDeck.objects.aget(id=reading.deck_id)
            
            # Сортировка и сбор данных (как мы делали раньше)
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
                    "flipped": flip_map.get(card_id_str, False) # Возвращаем flip сюда!
                }
            
            # Собираем список с сохраненным флагом flipped
            all_cards = [cards_dict[cid] for cid in order_list if cid in cards_dict]
            
            total_query = TarotCardItem.objects.filter(deck_id=current_deck.id)
            if reading.is_major_only:
                total_query = total_query.filter(tarot_card__is_major=True)
            total_cards = await total_query.acount()
            can_draw_query = total_query.exclude(tarot_card__card_id__in=order_list)
            can_draw = await can_draw_query.aexists()
            current_count = len(all_cards)

            card_names = [format_card_name(c['name'], c['flipped']) for c in all_cards]
            text = [
                f"<b>Расклад из колоды</b>: <a href='{current_deck.link}'>{escape(current_deck.name)}</a>\n",
                f"<b>Карты:</b> {escape(', '.join(card_names))}",
                f"\n<i>Всего в колоде: {current_count}/{total_cards}</i>"
            ]
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
                id__in=order_list # Тут ID — это первичный ключ OraculumItem
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
            
            card_names = [format_card_name(c['name'], c['flipped']) for c in all_cards]
            text = [
                f"<b>{escape(current_deck.name)}</b>",
                f"<b>Карты:</b> {', '.join(card_names)}",
                f"\n<i>Всего в колоде: {current_count}/{total_cards}</i>"
            ]
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
        
    def parse_reading_options(self, msg_text: str) -> dict:
        """
        Полный парсинг аргументов команды из текста сообщения.
        Поддерживает: /card3, deck 5, flip, major, c12_15_23.
        Все, что осталось после очистки служебных флагов — оригинальный запрос.
        """
        # Сохраняем исходную строку для вырезания флагов
        clean_text = msg_text

        msg_lower = msg_text.lower()
        options = {}

        # 1. Парсинг количества карт/рун (/card3, /oraculum6)
        counter_found = re.search(r"/[a-zA-Z]+(\d+)", msg_lower)
        options["counter"] = int(counter_found.group(1)) if counter_found else 1
        options["counter"] = max(1, min(options["counter"], 10))

        # Вырезаем саму команду (например, /card3 или /card)
        # Ищем команду с необязательными цифрами на конце
        clean_text = re.sub(r"/[a-zA-Z]+\d*", "", clean_text, flags=re.IGNORECASE)

        # 2. Парсинг ID колоды (deck 5)
        deck_match = re.search(r"deck\s*(\d+)", msg_lower)
        if deck_match:
            options["deck"] = int(deck_match.group(1))
            # Вырезаем 'deck X' или 'deckX' из текста запроса
            clean_text = re.sub(r"deck\s*\d+", "", clean_text, flags=re.IGNORECASE)
        else:
            options["deck"] = None

        # 3. Парсинг флага перевернутых позиций (flip)
        options["flip"] = "flip" in msg_lower
        if options["flip"]:
            clean_text = re.sub(r"\bflip\b", "", clean_text, flags=re.IGNORECASE)

        # 4. Парсинг флага Старших Арканов (major)
        options["major"] = "major" in msg_lower
        if options["major"]:
            clean_text = re.sub(r"\bmajor\b", "", clean_text, flags=re.IGNORECASE)

        # 5. Парсинг конкретных ID карт (формат: c12_15_23)
        card_ids_found = re.findall(r"[cC](\d+(?:_\d+)*)", msg_text)

        if card_ids_found:
            card_ids = [int(c) % 78 for c in card_ids_found[0].split("_")]
            target_count = options["counter"]

            if len(card_ids) < target_count:
                temp_id = card_ids[-1]
                for _ in range(len(card_ids), target_count):
                    temp_id = (temp_id + 1) % 78
                    card_ids.append(temp_id)

            options["card_ids"] = card_ids[:target_count]
            logger.info(f"Парсинг ID карт: card_ids={options['card_ids']}")

            # Вырезаем блок кастомных ID (например, c12_15_23)
            clean_text = re.sub(r"[cC]\d+(?:_\d+)*", "", clean_text)
        else:
            options["card_ids"] = None
            logger.info("ID карт не указаны, будут выбраны случайные карты.")

        # 6. ФИНАЛЬНАЯ ОЧИСТКА ОРИГИНАЛЬНОГО ЗАПРОСА
        # Убираем лишние пробелы, переносы строк и знаки препинания, которые могли остаться по краям
        clean_text = re.sub(r"\s+", " ", clean_text).strip()

        # Записываем результат (если пользователь ничего не ввел, будет пустая строка)
        options["original_query"] = clean_text

        logger.info(
            f"Опции расклада полностью собраны: counter={options['counter']}, "
            f"deck={options['deck']}, flip={options['flip']}, "
            f"major={options['major']}, has_custom_ids={options['card_ids'] is not None} | "
            f"Query: '{options['original_query']}'"
        )

        return options

    async def check_reading_cooldown(self, update: Update, category: str) -> bool:
        """
        Проверяет, есть ли активный кулдаун на гадание для пользователя.
        Возвращает True, если гадание ЗАБЛОКИРОВАНО (надо подождать).
        Возвращает False, если гадание ДОСТУПНО.
        """
        user_id = update.effective_user.id
        user = update.effective_user

        # Формируем ключ по тому же шаблону, что и при сохранении
        redis_key = REDIS_KEY_TEMPLATE.format(user_id=user_id, category=category)
        # Ключ для хранения ID сообщения кулдауна
        msg_ttl_key = f"user:ttl:message:{user_id}:{category}"

        try:
            # Запрашиваем оставшееся время жизни ключа (в секундах)
            time_left = await redis_client.ttl(redis_key)

            # Redis возвращает:
            # -1, если ключ существует, но у него нет TTL (бессрочный)
            # -2, если ключа нет в базе (кулдауна нет, можно гадать)
            if time_left > 0:
                # Красиво форматируем категорию (например, tarot -> ТАРОТ)
                category_upper = category.upper() 

                user_name = user.username or user.first_name or str(user_id)
                logger.info(
                    f"Пользователь {user_name} (id: {user_id}) "
                    f"пытается пойти раньше кулдауна на {time_left} секунд "
                    f"для категории {category_upper}"
                )

                # Проверяем все остальные категории на наличие активного кулдауна
                available_commands = []

                # Словарь соответствия категорий командам
                category_to_command = {
                    UserReading.ReadingCategory.ONE: "/one",
                    UserReading.ReadingCategory.TAROT: "/card",
                    UserReading.ReadingCategory.ORACLE: "/oraculum",
                    UserReading.ReadingCategory.RUNES: "/futark",
                    UserReading.ReadingCategory.CANVAS_SPREAD: "/spread",
                }

                # Проверяем каждую категорию
                for cat_choice in UserReading.ReadingCategory.values:
                    if cat_choice == category:
                        continue  # Пропускаем текущую заблокированную категорию

                    # Формируем ключ для проверки
                    check_key = REDIS_KEY_TEMPLATE.format(user_id=user_id, category=cat_choice)
                    check_ttl = await redis_client.ttl(check_key)

                    # Если ключа нет (time_left == -2) - категория доступна
                    if check_ttl == -2:
                        command = category_to_command.get(cat_choice)
                        if command:
                            available_commands.append(command)

                # === ИЩЕМ ДРУГИХ БОТОВ В REDIS ===
                all_bots = await redis_client_bot.hgetall("running_bots")
                other_bots = set() 

                for bot_id, bot_info_json in all_bots.items():
                    bot_info = json.loads(bot_info_json)
                    # Ищем ботов типа TarotBot
                    if (
                        bot_info.get('type') == 'TarotBot' and
                        bot_info.get('bot_id') != self.app_bot_id
                        ): 
                        bot_username = bot_info.get('username')
                        if bot_username:
                            other_bots.add(f"@{bot_username}")

                message_parts = [f"⚠️ Подождите {time_left} секунд до гадания {category_upper}"]

                if available_commands:
                    commands_text = ", ".join(available_commands)
                    message_parts.append(f"💡 Вы можете попробовать: {commands_text}")

                if other_bots:
                    bots_text = ", ".join(other_bots)
                    message_parts.append(f"🤖 Или попробуйте в других ботах: {bots_text}")

                if not available_commands and not other_bots:
                    message_parts.append("❌ Все команды на кулдауне")

                message = "\n\n".join(message_parts)

                # command_text = update.message.text
                # if command_text:
                #     hide_msg = await update.effective_message.reply_text(
                #         ".",
                #         reply_markup=ReplyKeyboardMarkup(
                #             [[KeyboardButton(command_text[:100])]],
                #             resize_keyboard=True,
                #             one_time_keyboard=True
                #         )
                #     )
                #     await hide_msg.delete()

                # === ОБНОВЛЕНИЕ ИЛИ ОТПРАВКА СООБЩЕНИЯ ===
                # Проверяем, есть ли уже отправленное сообщение об этом кулдауне
                existing_msg_id = await redis_client.get(msg_ttl_key)

                if existing_msg_id:
                    try:
                        # Используем update.get_bot() для вызова edit_message_text
                        await update.get_bot().edit_message_text(
                            chat_id=update.effective_chat.id,
                            message_id=int(existing_msg_id),
                            text=message
                        )
                        # ОБНОВЛЯЕМ TTL: перезаписываем тот же ID с актуальным остатком времени,
                        # чтобы ключ в Redis не удалился раньше времени
                        await redis_client.set(msg_ttl_key, existing_msg_id, ex=time_left)
                        await update.effective_message.delete()

                    except Exception as edit_err:
                        # Если сообщение удалено или текст совпадает, отправляем заново
                        logger.warning(
                            f"Не удалось отредактировать сообщение {existing_msg_id}: {edit_err}"
                        )
                        existing_msg_id = None

                if not existing_msg_id:
                    # Если сообщения не было или не удалось отредактировать — отправляем новое
                    sent_msg = await update.effective_message.reply_text(message)
                    # Сохраняем ID сообщения в Redis с TTL, равным остатку кулдауна
                    await redis_client.set(msg_ttl_key, sent_msg.message_id, ex=time_left)

                return True # Блокировка активна

        except Exception as e:
            # Если Redis упал, не блокируем пользователя, а логируем ошибку
            import logging
            logging.error(f"Ошибка проверки TTL в Redis: {e}")

        return False
    
    async def handle_card(self, update: Update, context: CallbackContext):
        """
        Обработчик команды /card.
        """
        msg_text = update.message.text
        logger.info(f"Обработка команды /card с текстом: {msg_text[:100]}")

        category = UserReading.ReadingCategory.TAROT
        if await self.check_reading_cooldown(update, category):
            return

        try:
            options = self.parse_reading_options(msg_text)
            logger.info(f"Опции расклада разобраны: {options}")

            # 1. Получение колоды
            deck = await self.get_deck(options.get("deck"))
            logger.info(f"Используемая колода ID: {deck.id if deck else 'None'}")

            # 2. Генерация карт
            cards = await self.get_cards(
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
            user = await self.get_or_create_tg_user(update)
            reading = await self.save_reading(
                user=user,
                message_id=update.effective_message.message_id,
                text=f"{deck.name if deck else 'Дефолтная колода'}: " + 
                     ", ".join([await self.format_card_name(c) for c in cards]),
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
            
            if await self.ai_interpret_handler.should_add_ai_button():
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

        except Exception as e:
            logger.error(f"Ошибка при обработке команды /card: {e}", exc_info=True)
            await update.message.reply_text("Произошла ошибка при выполнении расклада.")

    async def handle_more_button(self, update: Update, context: CallbackContext):
        query = update.callback_query
        await query.answer()
        logger.info(f"Получен callback-запрос: {query.data}")

        try:
            _, reading_id = query.data.split("_")
            user = await self.get_or_create_tg_user(update)

            # Ищем существующий расклад
            reading = await UserReading.objects.filter(id=reading_id, user=user).afirst()

            # УСЛОВИЕ: Если расклада нет — чистим кнопки и выходим
            if not reading:
                logger.warning(f"Расклад {reading_id} не найден.")
                await query.edit_message_text("Этот расклад больше не доступен.")
                await query.edit_message_reply_markup(reply_markup=None)
                return

            # === Подготовка данных ===
            exclude_cards = [str(item.get("id") if isinstance(item, dict) else item) for item in (reading.card_ids or [])]
            logger.info(f"Получаем карты с major {bool(reading.is_major_only)} flip {bool(reading.is_flipped_allowed)}. Исключаем: {exclude_cards}")

            new_card = await self.get_cards(
                deck_id=reading.deck_id,
                counter=1,
                exclude_cards=exclude_cards,
                major=bool(reading.is_major_only),
                flip=bool(reading.is_flipped_allowed)
            )

            if not new_card:
                await query.edit_message_text("Больше карт в колоде нет.")
                await query.edit_message_reply_markup(reply_markup=None)
                return

            # === Обновление записи ===
            new_card_text = ", ".join([await self.format_card_name(c) for c in new_card])
            new_card_data = [{"id": c["card_id"], "flip": c["flipped"]} for c in new_card]
            logger.info(f"Выбраны новые карты: {[c['name'] + ' ' + str(c['flipped']) for c in new_card]}")

            reading.text = f"{reading.text}, {new_card_text}"
            reading.count += 1
            reading.card_ids.extend(new_card_data)
            await reading.asave()

            # === Отправка результата ===
            send_card_kwargs = {"reading_id": reading.id, "send_type": "tarot"}
            if await self.ai_interpret_handler.should_add_ai_button():
                send_card_kwargs["add_ai_button"] = "🔮 Растолковать расклад (ИИ)"

            await self.send_card(update, new_card, **send_card_kwargs)

            # Очистка кнопок у старого сообщения
            await query.edit_message_reply_markup(reply_markup=None)

        except Exception as e:
            logger.error(f"Ошибка при обработке добора карты: {e}", exc_info=True)
            await query.edit_message_text("Произошла ошибка при доборе карты.")

    async def handle_oraculum(self, update: Update, context: CallbackContext):
        msg_text = update.message.text
        logger.info(f"Обработка команды /oraculum: {msg_text[:100]}")

        category = UserReading.ReadingCategory.ORACLE
        if await self.check_reading_cooldown(update, category):
            return

        try:
            options = self.parse_reading_options(msg_text)
            
            # 1. Получение колоды
            deck = await self.get_deck(options.get("deck"), "oraculum")
            
            # 2. Получение карт
            cards = await self.get_oraculum_cards(
                deck.id if deck else None, 
                options.get("counter", 1), 
                [],
                options.get('flip', False)
            )
            
            # 3. Сохранение расклада (используем консистентный формат card_ids)
            user = await self.get_or_create_tg_user(update)
            # Для оракула тоже используем формат списка словарей для единства БД
            card_records = [{"id": str(c["card_id"]), "flip": c["flipped"]} for c in cards]
            
            reading = await self.save_reading(
                user=user,
                message_id=update.effective_message.message_id,
                text=f"{deck.name if deck else 'Дефолтный оракул'}: " + 
                     ", ".join([await self.format_card_name(c) for c in cards]),
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

        except Exception as e:
            logger.error(f"Ошибка при обработке команды /oraculum: {e}", exc_info=True)
            await update.message.reply_text("Произошла ошибка при обработке запроса.")
            
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
                await query.edit_message_text("Этот расклад больше не доступен.")
                return
            
            # 2. Подготовка исключений (извлекаем card_ids из БД)
            exclude_cards = [
                str(item.get("id") if isinstance(item, dict) else item) 
                for item in (reading.card_ids or [])
            ]
            logger.info(f"Получаем карты оракула с major {bool(reading.is_major_only)} flip {bool(reading.is_flipped_allowed)}. Исключаем: {exclude_cards}")

            # 3. Генерация карты (используем настройки из самого расклада)
            new_card = await self.get_oraculum_cards(
                deck_id=reading.deck_id,
                counter=1,
                exclude_cards=exclude_cards,
                flip=reading.is_flipped_allowed
            )

            if not new_card:
                await query.edit_message_text("Больше карт в колоде нет.")
                return

            # 4. Обновление БД
            new_card_data = [{"id": str(c["card_id"]), "flip": c["flipped"]} for c in new_card]
            new_card_text = ", ".join([await self.format_card_name(c) for c in new_card])
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
            await query.edit_message_text("Ошибка при обработке запроса.")
            
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
        self,
        meaning_type,
        current_card,
        total_cards,
        current_page,
        total_pages,
    ):
        try:
            current_card = int(current_card)
            card_id = total_cards[current_card]
            logger.info(
                f"Создание клавиатуры для card_id={card_id}, тип {meaning_type}, страница {current_page} из {total_pages}"
            )

            keyboard = []
            base_context = {"meaning": meaning_type, "card": current_card, "page": 1}

            # Кнопки навигации
            paged_row = []
            if current_page > 1:
                paged_row.append(
                    ["← Назад", {**base_context, "page": current_page - 1}]
                )
            if current_page < total_pages:
                paged_row.append(
                    ["Вперед →", {**base_context, "page": current_page + 1}]
                )

            # Загружаем значения с prefetch_related
            all_meanings = ExtendedMeaning.objects.prefetch_related(
                "tarot_card", "category_base"
            ).filter(tarot_card__card_id=card_id)

            # Собираем все значения в список
            meanings_list = [
                (item.category_base.id, item.category_base.name)
                async for item in all_meanings
            ]
            meanings_list.append(("base", "Базовый"))
            meanings_list.sort(key=lambda x: x[1])
            current_idx = next(
                (
                    i
                    for i, (cat_id, _) in enumerate(meanings_list)
                    if str(cat_id) == str(meaning_type)
                ),
                -1,
            )
            if current_idx == -1:
                logger.warning(
                    f"Категория '{meaning_type}' не найдена. Используем первую."
                )
                current_idx = 0
                meaning_type = meanings_list[0][0]

            # 🔁 Зацикленная навигация: граничные случаи ОБРАБОТАНЫ автоматически через %
            prev_idx = (current_idx - 1) % len(meanings_list)
            next_idx = (current_idx + 1) % len(meanings_list)

            meaning_prev = meanings_list[prev_idx]  # (id, name)
            meaning_next = meanings_list[next_idx]

            meaning_row = [
                [meaning_prev[1], {**base_context, "meaning": meaning_prev[0]}],
                [meaning_next[1], {**base_context, "meaning": meaning_next[0]}],
            ]

            cards_row = []
            if len(total_cards) > 1:
                card_prev = current_card - 1 if current_card > 0 else None
                card_next = (
                    current_card + 1 if current_card < len(total_cards) - 1 else None
                )

                if card_prev:
                    prev_card_id = total_cards[card_prev]
                    prev_card = await TarotCard.objects.aget(card_id=prev_card_id)
                    cards_row.append(
                        [
                            f"← {prev_card.name}",
                            {**base_context, "meaning": "base", "card": card_prev},
                        ]
                    )

                if card_next:
                    next_card_id = total_cards[card_next]
                    next_card = await TarotCard.objects.aget(card_id=next_card_id)
                    cards_row.append(
                        [
                            f"{next_card.name} →",
                            {**base_context, "meaning": "base", "card": card_next},
                        ]
                    )

            for row in [paged_row, meaning_row, cards_row]:
                if not row:
                    continue
                keyboard.append(
                    [
                        InlineKeyboardButton(
                            text=item[0],
                            callback_data="_".join(
                                [
                                    "meaning",
                                    str(item[1].get("meaning", meaning_type)),
                                    str(item[1].get("card", current_card)),
                                    "#".join(map(str, total_cards)),
                                    str(item[1].get("page", 1)),
                                ]
                            ),
                        )
                        for item in row
                    ]
                )

            return InlineKeyboardMarkup(keyboard)

        except Exception as e:
            logger.error(f"Ошибка в create_pagination_keyboard: {e}", exc_info=True)
            return None  # Возвращаем None, если произошла критическая ошибка

    # Функция для отправки текста с пагинацией
    async def send_paginated_text(self, update: Update, cards, card_index, text):
        # Разделяем текст на части
        card_id = cards[card_index]

        text_parts = self.split_text(text)
        total_pages = len(text_parts)
        current_page = 1

        base_card = await TarotCard.objects.aget(card_id=card_id)

        keyboard = await self.create_pagination_keyboard(
            "base",
            card_index,
            cards,
            current_page,
            total_pages,
        )
        # Отправляем первую страницу
        await update.effective_message.reply_text(
            text=(
                f"<strong>{base_card.name}</strong>\n"
                f"Базовый\n"
                f"стр 1/{len(text_parts)}\n"
                "\n"
                f"{text_parts[0]}"
            ),
            reply_markup=keyboard,
            parse_mode="HTML",
        )

    # Обработчик для навигации по страницам
    async def handle_pagination(self, update: Update, context: CallbackContext):
        query = update.callback_query
        await query.answer()

        # Извлекаем данные из callback_data
        _, meaning_type, card_index, cards, page = query.data.split("_")
        page = int(page)
        card_index = int(card_index)
        cards = [int(i) for i in cards.split("#")]
        card_id = cards[card_index]

        base_card = await TarotCard.objects.aget(card_id=card_id)

        if meaning_type != "base":
            extended_cards = ExtendedMeaning.objects.all().prefetch_related(
                "tarot_card", "category_base"
            )
            extended_card = await extended_cards.filter(
                tarot_card__card_id=card_id, category_base=meaning_type
            ).aget()
        if meaning_type == "base":
            text = base_card.meaning
        else:
            text = extended_card.text

        text_parts = self.split_text(text)
        keyboard = await self.create_pagination_keyboard(
            meaning_type, card_index, cards, page, len(text_parts)
        )

        # Обновляем сообщение с новой страницей
        await query.edit_message_text(
            text=(
                f"<strong>{base_card.name}</strong>\n"
                f"{'Базовый' if meaning_type == 'base' else extended_card.category_base}\n"
                f"стр {page}/{len(text_parts)}\n"
                "\n"
                f"{text_parts[page - 1]}"
            ),
            reply_markup=keyboard,
            parse_mode="HTML",
        )

    async def handle_desc_button(self, update: Update, context: CallbackContext):
        query = update.callback_query
        await query.answer()

        _, cards = query.data.split("_")
        cards = cards.split("#")

        card = await TarotCard.objects.aget(card_id=cards[0])
        text = card.name + "\n" + card.meaning
        await self.send_paginated_text(update, [int(x) for x in cards], 0, text)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((ClientError, asyncio.TimeoutError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        after=after_log(logger, logging.DEBUG),
        reraise=True
    )
    async def load_page(self, url):
        timeout = ClientTimeout(total=5) 
        async with ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                return await response.text()

    async def handle_one_command(self, update: Update, context: CallbackContext):
        category = UserReading.ReadingCategory.ONE
        is_locked = await self.check_reading_cooldown(update, category)
        if is_locked:
            return

        tarot_url = "https://www.tarot.com"
        decks_url = "/tarot/decks"

        tech_msg = await update.effective_message.reply_text(
            "Выбираю колоду", reply_to_message_id=update.effective_message.message_id
        )
        tech_msg_id = tech_msg.message_id

        content = await self.load_page(f"{tarot_url}{decks_url}")
        dom = BeautifulSoup(content, "html.parser")

        decks_raw = dom.select(".tarot-deck-list a")
        decks = [el["href"] for el in decks_raw if el.get("href")]

        random_deck_id = random.randint(0, len(decks) - 1)
        random_deck = decks[random_deck_id]

        await context.bot.edit_message_text(
            "Выбираю карту",
            chat_id=update.effective_chat.id,  # ID чата
            message_id=tech_msg_id,  # ID сообщения, которое нужно отредактировать
        )

        content = await self.load_page(f"{tarot_url}{random_deck}")
        dom = BeautifulSoup(content, "html.parser")

        cards_raw = dom.select('#majorarcana ~ row a[data-category*="Tarot Decks:"]')
        cards = []
        for el in cards_raw:
            name = el.text.strip()
            img = el.find("img")["src"]
            if "mid_size" in img:
                img = img.replace("mid_size", "full_size")
            url = f"{tarot_url}{el['href']}"
            cards.append({"name": name, "url": url, "img": img})

        random_card_id = random.randint(0, len(cards) - 1)
        random_card = cards[random_card_id]

        user = await self.get_or_create_tg_user(update)
        await self.save_reading(
            user=user,
            message_id=update.effective_message.message_id,
            text=f"{random_card['name']}\n{random_card['url']}",
            category=category
        )

        await update.effective_message.reply_photo(
            random_card["img"],
            f"{random_card['name']}\n{random_card['url']}",
            reply_to_message_id=update.effective_message.message_id,
        )
        await context.bot.delete_message(update.effective_chat.id, tech_msg_id)

    async def handle_last_readings(self, update: Update, context: CallbackContext):
        """
        Обработчик команды истории последних 5 гаданий.
        """
        logger.info(f"Запрос истории гаданий для пользователя: {update.effective_user.id}")

        try:
            # 1. Безопасно получаем или создаем пользователя одной строкой
            user = await self.get_or_create_tg_user(update)
            if not user:
                return

            user_readings = []
            count = 5

            # 2. Выбираем последние 5 записей из новой модели
            # Используем префикс даты для вывода в чат
            async for item in (
                UserReading.objects.filter(user=user)
                .order_by("-created_at")[:count]
            ):
                # Форматируем дату для читаемости (например: 12.06.2026 13:30)
                formatted_date = item.created_at.strftime("%d.%m.%Y %H:%M")
                user_readings.append(f"📅 {formatted_date}\n{item.text[0:200]}\n")

            if not user_readings:
                await update.effective_message.reply_text("Гаданий не найдено.")
                return

            # 3. Отправляем красивый структурированный список
            await update.effective_message.reply_text(
                "📜 <b>Ваши последние {count} гаданий:</b>\n\n" + "\n".join(user_readings),
                parse_mode=ParseMode.HTML,
                reply_to_message_id=update.effective_message.message_id,
            )

        except Exception as e:
            logger.error(f"Ошибка при получении истории гаданий: {e}", exc_info=True)

    async def make_decks_page(
        self,
        current_page: int = 0,
        items_per_page: int = 13,
        deck_type: str = "tarot",
        mode: str = "screen"
    ):
        """
        Формирует страницу с колодами и inline-клавиатуру для пагинации.
        """
        # Выбираем модель колоды в зависимости от типа
        if deck_type == "oraculum":
            all_decks = OraculumDeck.objects.all().order_by("id")
        elif deck_type == "tarot":
            all_decks = TarotDeck.objects.all().order_by("id")
        else:
            raise ValueError("Неизвестный тип колоды")

        all_decks_count = await all_decks.acount()

        # Разбиваем колоды на страницы
        decks_pages = [
            all_decks[i : i + items_per_page]
            for i in range(0, all_decks_count, items_per_page)
        ]

        # Формируем текст текущей страницы
        if current_page >= len(decks_pages):
            current_page = 0  # Если страница выходит за пределы, возвращаемся на первую

        decks_page = decks_pages[current_page]
        decks_text = []
        command_name = "card"
        if deck_type == "oraculum":
            command_name = "oraculum"
        async for deck in decks_page:
            decks_text.append(
                f"<code>/{command_name} deck {deck.id}</code> - {deck.name}"
            )

        # Если режим возврата данных
        if mode == "return":
            return decks_page, current_page, len(decks_pages)

        decks_text = "\n".join(decks_text)

        keyboard = []
        # Добавляем кнопку "Назад", если есть предыдущая страница
        if current_page > 0:
            keyboard.append(
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=f"deckspage_{current_page - 1}_{deck_type}",
                )
            )

        # Добавляем кнопку "Вперед", если есть следующая страница
        if current_page < len(decks_pages) - 1:
            keyboard.append(
                InlineKeyboardButton(
                    text="➡️ Вперед",
                    callback_data=f"deckspage_{current_page + 1}_{deck_type}",
                )
            )

        return decks_text, InlineKeyboardMarkup([keyboard])

    async def handle_decks(self, update: Update, context: CallbackContext):
        """
        Обработчик команды /decks.
        """
        try:
            msg_text = update.message.text
            logger.info(f"Обработка команды /oraculum с текстом: {msg_text[:100]}")

            # Определяем тип колоды (по умолчанию — oraculum)
            deck_type = "tarot"
            if bool(re.search(r"oraculum", msg_text)):
                deck_type = "oraculum"

            # Формируем первую страницу с колодами
            decks_text, keyboard = await self.make_decks_page(
                current_page=0, deck_type=deck_type
            )

            # Отправляем сообщение с колодами и inline-клавиатурой
            await update.message.reply_text(
                decks_text,
                reply_markup=keyboard,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.error(f"Ошибка при обработке команды /decks: {e}", exc_info=True)
            await update.message.reply_text(
                "Произошла ошибка при обработке вашего запроса. Пожалуйста, попробуйте снова."
            )

    async def handle_decks_page(self, update: Update, context: CallbackContext):
        """
        Обработчик callback-запросов для переключения между страницами колод.
        """
        query = update.callback_query
        await query.answer()

        try:
            # Получаем номер страницы и тип колоды из callback_data
            _, page_number, deck_type = query.data.split("_")
            page_number = int(page_number)

            # Формируем новую страницу с колодами
            decks_text, keyboard = await self.make_decks_page(
                current_page=page_number, deck_type=deck_type
            )

            # Редактируем сообщение с новой страницей
            await query.edit_message_text(
                decks_text,
                reply_markup=keyboard,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.error(f"Ошибка при обработке callback-запроса: {e}", exc_info=True)
            await query.edit_message_text(
                "Произошла ошибка. Пожалуйста, попробуйте снова."
            )

    async def handle_spread(self, update: Update, context: CallbackContext):
        msg_text = update.message.text
        user = await self.get_or_create_tg_user(update)
        logger.info(f"Обработка команды /spread с текстом: {msg_text[:100]}")

        category = UserReading.ReadingCategory.CANVAS_SPREAD
        is_locked = await self.check_reading_cooldown(update, category)
        if is_locked:
            return

        try:
            # Если все проверки пройдены
            tech_msg = await update.message.reply_text("Выбор карт")

            options = self.parse_reading_options(msg_text)

            deck = await self.get_deck(options.get("deck"))
            logger.info(f"Используемая колода: {deck.id if deck else 'не указана'}")

            cards = await self.get_cards(
                deck_id=deck.id if deck else None,
                counter=options["counter"],
                card_ids=options["card_ids"],
                major=options["major"],
                flip=options['flip'],
                exclude_cards=None,
            )

            cards_description = []
            for card_data in cards:
                parts = [card_data["name"]]
                if card_data["flipped"]:
                    parts.append("<i>перевернуто</i>")
                cards_description.append(" ".join(parts))

            logger.info(f"Получено карт: {len(cards)}")

            await self.save_reading(
                user=user,
                message_id=update.effective_message.message_id,
                text=f"{deck.name if deck else 'Дефолтная колода'}: " + ", ".join(
                    [await self.format_card_name(c) for c in cards]
                ),
                category=category,
                count=options["counter"],                             # Передаем точное количество карт
                deck_id=deck.id if deck else None,
                is_flipped_allowed=options.get('flip', False),
                is_major_only=options.get('major', False)
            )

            description_text = f"Расклад  из колоды {deck.name}:\n" + "\n".join(
                f"{' ' * 4}{desc}" for desc in cards_description
            )

            for card_data in cards:
                card_item = card_data["card_instance"]
                bot_file = await card_item.files.afirst()
                if not bot_file:
                    logger.warning(f"Нет исходного файла для карты {card_item.id}")
                    continue

                file_link = await BotFileCache.acreate_and_get_link(bot_file=bot_file)

                if file_link:
                    # Можно скачать файл или сохранить путь для отправки
                    card_data["file_path"] = file_link
                    logger.info(f"Готов к отправке файл для карты {card_item.id}: {file_link}")
                else:
                    logger.warning(f"Не удалось создать кэш для карты {card_item.id}")

            await tech_msg.edit_text(
                "Загрузка и отрисовка",
            )
            spread_image = await create_spread_image(cards, options)

            if spread_image:
                # Отправляем изображение пользователю
                await tech_msg.edit_media(
                    media=InputMediaPhoto(media=spread_image, caption=description_text, parse_mode='HTML'),
                )
            else:
                await tech_msg.edit_text("❌ Не удалось создать изображение расклада")
                await update.message.reply_text(description_text)

        except Exception as e:
            logger.error(f"Ошибка при обработке команды /spread: {e}", exc_info=True)
            await update.message.reply_text(
                "Произошла ошибка при обработке вашего запроса. Пожалуйста, попробуйте снова."
            )

    async def handle_photo_msg(self, update: Update, context: CallbackContext):
        logger.info(update)

    async def handle_help(self, update: Update, context: CallbackContext):
        """
        Обработчик команды /help.
        """
        help_text = """
📜 <b>Доступные команды:</b>

<code>/one</code> - самая простая одна карта

🔮 <b>Таро:</b>
<code>/card</code> - Сделать расклад Таро (1 карта по умолчанию).
<code>/card3</code> - Сделать расклад из 3 карт.
<code>/card deck НОМЕР КОЛОДЫ</code> - Выбрать колоду для расклада.
<code>/card flip</code> - Сделать расклад с возможностью перевернутых карт.
<code>/card major</code> - Сделать расклад с использованием только старших арканов.
Команды можно комбинировать: <code>/card3 deck 5 major flip</code>

🌟 <b>Оракул:</b>
<code>/oraculum</code> - Сделать расклад Оракула (1 карта по умолчанию).
<code>/oraculum3</code> - Сделать расклад из 3 карт.
<code>/oraculum flip</code> - Сделать расклад с возможностью перевернутых карт.

🛡️ <b>Футарк:</b>
<code>/futark</code> - Сделать расклад одной руны.
<code>/futark triplet</code> - Сделать расклад из 3 рун.
<code>/futark flip</code> - Сделать расклад с возможностью перевернутых рун.

📚 <b>Колоды:</b>
<code>/decks</code> - Показать список колод Таро.
<code>/decks oraculum</code> - Показать список колод Оракула.

🖼️ <b>Расклад на холсте:</b>
<code>/spread</code> - Сделать расклад из 3 карт с генерацией изображения на холсте.
<code>/spread deck НОМЕР КОЛОДЫ</code> - Расклад из выбранной колоды.
<code>/spread flip ТЕКСТ</code> - Расклад с возможностью перевернутых карт.
Пример: <code>/spread deck 3 flip что меня ждёт завтра?</code>
Бот скачает карты, отрисует их на едином холсте и отправит изображением.

❓ <b>Помощь:</b>
<code>/help</code> - Показать это сообщение.

📌 <b>Примеры:</b>
<code>/card deck 1</code> - Сделать расклад из колоды Таро с ID 1.
<code>/oraculum3 flip</code> - Сделать расклад из 3 карт Оракула с возможностью перевернутых карт.
<code>/futark triplet</code> - Сделать расклад из 3 рун.
"""

        await update.message.reply_text(help_text, parse_mode="HTML")
