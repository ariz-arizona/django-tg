import django.db
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
from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import Q

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
    DeckSearch,
)
from tg_bot.models import BotFileCache
from server.logger import logger
from django.conf import settings

from tarot.utils.image_utils import create_spread_image
from tarot.bot.allcard_handler import AllCardHandler
from tarot.bot.ai_interpret_handler import AIInterpretHandler
from tarot.bot.rune_handler import RuneHandler
from tarot.bot.meaning_handler import MeaningHandler
from tarot.bot.cards_handler import CardsHandler

from tarot.messages import CanvasMessages, CANVAS_3_TRIGGER, TAROT_3_TRIGGER

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
        self.meaning_handler = MeaningHandler(self)
        self.cards_handler = CardsHandler(self)
        self.handlers = self.get_handlers()

    def get_handlers(self):
        return [
            MessageHandler(filters.PHOTO, self.handle_photo_msg),
            CommandHandler("start", self.handle_start),
            CommandHandler("help", self.handle_help),
            
            *self.allcard_handler.get_handlers(),
            *self.rune_handler.get_handlers(),
            *self.ai_interpret_handler.get_handlers(),
            *self.meaning_handler.get_handlers(),
            *self.cards_handler.get_handlers(),
            
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
                filters.Text([CANVAS_3_TRIGGER]) & filters.ChatType.PRIVATE,
                self.handle_spread
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


    def parse_reading_options(self, msg_text: str) -> dict:
        """
        Полный парсинг аргументов команды из текста сообщения.
        Поддерживает: /card3, deck 5, flip, major, c12_15_23.
        Все, что осталось после очистки служебных флагов — оригинальный запрос.
        """
        # Сохраняем исходную строку для вырезания флагов
        def hide_ids(match):
            return match.group(0).replace("_", "|||") # Заменяем _ на уникальный разделитель
        
        msg_text = re.sub(r"[cC]\d+(?:_\d+)*", hide_ids, msg_text)
        
        # 2. Теперь безопасно меняем остальные подчеркивания на пробелы
        msg_text = msg_text.replace("_", " ")
        
        # 3. Возвращаем ID карт обратно (меняем наш временный токен на _)
        msg_text = msg_text.replace("|||", "_")
        
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

        # 2. Парсинг ID колоды или поиск по слову
        deck_match = re.search(r"deck\s*(\S+)", msg_lower)
        if deck_match:
            deck_value = deck_match.group(1)
            
            # Пробуем как число
            if deck_value.isdigit():
                options["deck"] = int(deck_value)
            else:
                # Ищем по слову (slug или name)
                options["deck_keyword"] = deck_value
            
            clean_text = re.sub(r"deck\s*\S+", "", clean_text, flags=re.IGNORECASE)
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
            f"deck={options.get('deck', None)}, deck_keyword={options.get('deck_keyword', None)}, "
            f"flip={options['flip']}, major={options['major']}, has_custom_ids={options['card_ids'] is not None} | "
            f"Query: '{options['original_query']}'"
        )

        return options

    def parse_text_reading_options(self, msg_text: str) -> dict:
        """
        Парсинг аргументов из текстового сообщения.
        Форматы:
        - Таро 3 переверн старши колода "Викторианская"
        - tarot 5 flip major deck waite
        - таро 5 перевернуты старшие колода ленорман
        - таро переверни 10 колода уэйт
        - Таро 3 оригинальный запрос для ИИ
        
        Если нет явного ключевика "колода"/"deck", 
        то всё что не высеклось как counter/flip/major — уходит в deck_keyword.
        Если ключевик есть — всё после него в deck_keyword, остальное в original_query.
        """
        options = {}
        clean_text = msg_text.strip()
        
        # Убираем первое слово "Таро" или "tarot"
        clean_text = re.sub(r"^(таро|tarot)\s*", "", clean_text, flags=re.IGNORECASE).strip()
        
        remaining = clean_text
        
        # 1. Парсинг количества карт (цифра)
        counter = 1
        counter_match = re.search(r"\b(\d+)\b", remaining)
        if counter_match:
            counter = int(counter_match.group(1))
            counter = max(1, min(counter, 10))
            # Вырезаем цифру
            remaining = remaining[:counter_match.start()] + remaining[counter_match.end():]
            remaining = re.sub(r"\s+", " ", remaining).strip()
        options["counter"] = counter
        
        # 2. Парсинг флага перевернутых позиций (начинается на переверн/flip)
        options["flip"] = False
        flip_match = re.search(r"\b(переверн\w*|flip\w*)\b", remaining, flags=re.IGNORECASE)
        if flip_match:
            options["flip"] = True
            remaining = remaining[:flip_match.start()] + remaining[flip_match.end():]
            remaining = re.sub(r"\s+", " ", remaining).strip()
        
        # 3. Парсинг флага Старших Арканов (начинается на старш/major)
        options["major"] = False
        major_match = re.search(r"\b(старш\w*|major\w*)\b", remaining, flags=re.IGNORECASE)
        if major_match:
            options["major"] = True
            remaining = remaining[:major_match.start()] + remaining[major_match.end():]
            remaining = re.sub(r"\s+", " ", remaining).strip()
        
        # 4. Парсинг колоды
        options["deck"] = None
        options["deck_keyword"] = None
        options["original_query"] = ""
        
        deck_match = re.search(r"\b(колода|deck)\s+(.+)", remaining, flags=re.IGNORECASE)
        if deck_match:
            # Явный ключевик "колода/deck" — всё после него в deck_keyword
            deck_value = deck_match.group(2).strip()
            
            # Убираем кавычки если есть
            deck_value = deck_value.strip('"\"\'')
            
            if deck_value.isdigit():
                options["deck"] = int(deck_value)
            else:
                options["deck_keyword"] = deck_value
            
            # Всё до "колода/deck" — original_query
            original_query = remaining[:deck_match.start()].strip()
            options["original_query"] = original_query if original_query else ""
        else:
            # Нет ключевика "колода/deck" — всё оставшееся в deck_keyword
            remaining = remaining.strip()
            if remaining:
                # Проверяем, не число ли это (хотя числа уже вырезаны)
                if remaining.isdigit():
                    options["deck"] = int(remaining)
                else:
                    options["deck_keyword"] = remaining
        
        logger.info(
            f"Текстовый парсинг: counter={options['counter']}, "
            f"deck={options.get('deck')}, deck_keyword={options.get('deck_keyword')}, "
            f"flip={options['flip']}, major={options['major']} | "
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


    async def _log_search(self, deck_keyword: str, status: str, decks=None):
        found = None
        if decks is not None:
            if isinstance(decks, list):
                found = [
                    {"id": d.id, "name": d.name, "type": "tarot" if isinstance(d, TarotDeck) else "oraculum"}
                    for d in decks
                ]
            else:
                # Одна колода
                found = [
                    {"id": decks.id, "name": decks.name, "type": "tarot" if isinstance(decks, TarotDeck) else "oraculum"}
                ]
        
        await DeckSearch.objects.acreate(
            deck_keyword=deck_keyword,
            status=status,
            found_decks=found
        )
        
        
    async def get_deck(self, deck_id=None, deck_keyword=None, deck_type="tarot", return_all=False):
        """
        Возвращает колоду или список колод.
        
        Args:
            deck_id: ID колоды
            deck_keyword: ключевое слово для поиска
            deck_type: "tarot" или "oraculum"
            return_all: если True и keyword — возвращает список всех найденных колод
        """
        model = OraculumDeck if deck_type == "oraculum" else TarotDeck        
        deck_ids: List[int] = [deck.id async for deck in model.objects.all()]
        logger.info(f"Получаем колоду: id={deck_id}, keyword={deck_keyword}, type={deck_type}, return_all={return_all}")

        if not deck_ids:
            raise ValueError("Нет доступных колод.")

        if deck_keyword and deck_id is None:
            # 1. Точное совпадение по slug
            deck = await model.objects.filter(slug=deck_keyword).afirst()
            
            if deck:
                await self._log_search(deck_keyword, "success", [deck])
                if return_all:
                    return [deck]
                return deck

            # 2. Комбинированный поиск: ILIKE + триграммы для сортировки
            decks = model.objects.filter(
                Q(name__icontains=deck_keyword) |
                Q(slug__icontains=deck_keyword) |
                Q(seo_tags__icontains=deck_keyword)
            ).annotate(
                similarity=(
                    TrigramSimilarity('name', deck_keyword) + 
                    TrigramSimilarity('slug', deck_keyword)
                )
            ).order_by('-similarity')
            
            count = await decks.acount()
            
            if count > 0:
                if return_all:
                    deck_list = [d async for d in decks]
                    names = [(d.name, d.similarity) for d in deck_list]
                    await self._log_search(deck_keyword, "success", deck_list)
                    logger.info(f"Найдено {count} колод по '{deck_keyword}': {names}")
                    return deck_list
                else:
                    deck = await decks.afirst()
                    similarity = deck.similarity
                    await self._log_search(deck_keyword, "success", [deck])
                    logger.info(f"Колода найдена '{deck_keyword}': {deck.name} (similarity={similarity:.2f})")
                    if count > 1:
                        names = [(d.name, d.similarity) async for d in decks[:3]]
                        logger.warning(f"Найдено {count} колод по '{deck_keyword}': {names}")
                    return deck
            
            # 3. Ничего не найдено
            await self._log_search(deck_keyword, "not_found", None)
            if return_all:
                return []
            return None
            
            # Если не return_all, возвращаем одну колоду или None
            if not return_all:
                if deck:
                    return deck
                else:
                    logger.warning(f"Колода по ключевому слову '{deck_keyword}' не найдена")
                    return None
            else:
                # return_all=True, но дошли сюда только если были точные совпадения по slug
                return [deck] if deck else []

        # Дальше идём только если не return_all
        if return_all:
            await self._log_search(deck_keyword or "all", "not_found", None)
            return []

        # Поиск по ID
        if deck_id is not None and deck_id not in deck_ids:
            logger.error(f"Указанный ID колоды {deck_id} не существует.")
            await self._log_search(str(deck_id), "not_found", None)
            deck_id = None

        if deck_id is None and not deck_keyword:
            deck_id = random.choice(deck_ids)

        try:
            deck = await model.objects.aget(id=deck_id)
            await self._log_search(str(deck_id), "success", [deck])
            return deck
        except Exception as e:
            logger.error(f"Произошла ошибка при поиске колоды {e}", exc_info=True)
            await self._log_search(str(deck_id), "not_found", None)
            raise ValueError("Не удалось получить колоду.")

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
            category=category,
            count=1
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
            command = self.cards_handler.messages.build_deck_command(f"/{command_name}", deck.slug)
            decks_text.append(
                self.cards_handler.messages.get_deck_list_item(command, deck.name)
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
        messages = CanvasMessages()

        category = UserReading.ReadingCategory.CANVAS_SPREAD
        is_locked = await self.check_reading_cooldown(update, category)
        if is_locked:
            # Использование сообщения об ошибке через класс
            error_msg = self.messages.get_error_message("cooldown", wait_time="60")
            await update.message.reply_text(error_msg, parse_mode=ParseMode.HTML)
            return

        try:
            if msg_text == CANVAS_3_TRIGGER:
                options = {
                    "counter": 3,
                    "deck": None,
                    "flip": True,
                    "major": False,
                    "card_ids": None,
                    "original_query": ""
                }                
            else:
                options = self.parse_reading_options(msg_text)

            deck = await self.get_deck(options.get("deck"), options.get("deck_keyword", None))
            if not deck and options.get("deck"):
                error_msg = messages.get_error_message("no_deck")
                await update.message.reply_text(error_msg, parse_mode=ParseMode.HTML)
                return
            
            logger.info(f"Используемая колода: {deck.id if deck else 'не указана'}")
            
            # Использование сообщений через класс
            tech_msg = await update.message.reply_text(
                messages.get_initializing(), 
                parse_mode=ParseMode.HTML,
                reply_to_message_id=update.effective_message.message_id
            )

            cards = await self.get_cards(
                deck_id=deck.id if deck else None,
                counter=options["counter"],
                card_ids=options["card_ids"],
                major=options["major"],
                flip=options['flip'],
                exclude_cards=None,
            )
            
            if not cards:
                error_msg = messages.get_error_message("no_cards")
                await tech_msg.edit_text(error_msg, parse_mode=ParseMode.HTML)
                return

            cards_description = []
            for card_data in cards:
                parts = [card_data["name"]]
                if card_data["flipped"]:
                    parts.append("<i>перевернуто</i>")
                cards_description.append(" ".join(parts))

            card_records = [{"id": str(c["card_id"]), "flip": c["flipped"]} for c in cards]
            logger.info(f"Получены карты {card_records}")

            await self.save_reading(
                user=user,
                message_id=update.effective_message.message_id,
                text=f"{deck.name if deck else 'Дефолтная колода'}: " + ", ".join(
                    [await self.format_card_name(c) for c in cards]
                ),
                category=category,
                count=options["counter"],
                deck_id=deck.id if deck else None,
                is_flipped_allowed=options.get('flip', False),
                is_major_only=options.get('major', False),
                card_ids=card_records
            )

            description_text = messages.format_description(
                deck.name if deck else None, 
                cards_description
            )
            
            await tech_msg.edit_text(
                f"{messages.get_loading()}\n\n{description_text}", 
                parse_mode=ParseMode.HTML
            )

            for card_data in cards:
                card_item = card_data["card_instance"]
                bot_file = await card_item.files.afirst()
                if not bot_file:
                    error_msg = messages.get_error_message(
                        "file_not_found", 
                        card_name=card_data["name"]
                    )
                    logger.warning(f"Нет исходного файла для карты {card_item.id}")
                    continue

                file_link = await BotFileCache.acreate_and_get_link(bot_file=bot_file)

                if file_link:
                    card_data["file_path"] = file_link
                    logger.info(f"Готов к отправке файл для карты {card_item.id}: {file_link}")
                else:
                    logger.warning(f"Не удалось создать кэш для карты {card_item.id}")

            await tech_msg.edit_text(
                f"{messages.get_rendering()}\n\n{description_text}",
                parse_mode=ParseMode.HTML
            )
            
            try:
                spread_image = await create_spread_image(cards, options)
            except Exception as img_error:
                error_msg = messages.get_error_message("image_failed")
                await tech_msg.edit_text(error_msg, parse_mode=ParseMode.HTML)
                logger.error(f"Ошибка создания изображения: {img_error}")
                return
            
            await tech_msg.edit_text(
                f"{messages.get_uploading()}\n\n{description_text}",
                parse_mode=ParseMode.HTML
            )
            
            if spread_image:
                await tech_msg.edit_media(
                    media=InputMediaPhoto(media=spread_image, caption=description_text, parse_mode=ParseMode.HTML),
                )
            else:
                error_msg = messages.get_error_message("image_failed")
                await tech_msg.edit_text(error_msg, parse_mode=ParseMode.HTML)

        except ValueError as ve:
            logger.error(f"Ошибка валидации: {ve}")
            error_msg = messages.get_error_message("invalid_options")
            await update.message.reply_text(error_msg, parse_mode=ParseMode.HTML)
            
        except Exception as e:
            logger.error(f"Ошибка при обработке команды /spread: {e}", exc_info=True)
            error_msg = self.messages.get_error_message("generic", error_details=str(e)[:100])
            await update.message.reply_text(error_msg, parse_mode=ParseMode.HTML)
            
    async def handle_photo_msg(self, update: Update, context: CallbackContext):
        logger.info(update)

    def default_reply_keyboard(self):
        return ReplyKeyboardMarkup(
            [[TAROT_3_TRIGGER, CANVAS_3_TRIGGER]],
            resize_keyboard=True,
            one_time_keyboard=False,
            input_field_placeholder="Выберите расклад..."
        )
        
    async def handle_start(self, update: Update, context: CallbackContext):
        start_text = """
🔮 <b>Добро пожаловать!</b>

Я помогу вам сделать расклад Таро, Оракула или рун.

Нажмите кнопку ниже — или введите /help для полного списка команд.
"""
        reply_markup = self.default_reply_keyboard()
        await update.message.reply_text(
            start_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
        
    async def handle_help(self, update: Update, context: CallbackContext):
        help_text = """
📜 <b>Доступные команды:</b>

/one - самая простая одна карта

🔮 <b>Таро:</b>
/card - Сделать расклад Таро.
/card3 - Сделать расклад из 3 карт.
/card_deck_1 - Расклад из выбранной колоды.
/card_flip - С возможностью перевернутых карт.
/card_major - Только старшие арканы.
Комбинируй: /card3_deck_5_major_flip

🌟 <b>Оракул:</b>
/oraculum - Расклад Оракула.
/oraculum3 - Расклад из 3 карт.
/oraculum_flip - С перевернутыми картами.

🛡️ <b>Футарк:</b>
/futark - Одной руны.
/futark_triplet - Из 3 рун.
/futark_flip - С перевернутыми рунами.

📚 <b>Колоды:</b>
/decks - Список колод Таро.
/decks_oraculum - Список колод Оракула.

🖼️ <b>Расклад на холсте:</b>
/canvas - Расклад из 3 карт на холсте.
/canvas6_deck_3_flip - Расклад с настройками.
Пример: /canvas6_deck_3_flip

❓ <b>Помощь:</b>
/help - Показать это сообщение.
"""

        await update.message.reply_text(
            help_text, 
            parse_mode=ParseMode.HTML
        )
