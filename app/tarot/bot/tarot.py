import django.db
import re
import os
from typing import List, Optional, Dict
from datetime import timedelta

import asyncio
import json
import redis.asyncio as aioredis
import random
from bs4 import BeautifulSoup

from telegram import (
    Update, InlineKeyboardButton,
    InlineKeyboardMarkup, ReplyKeyboardMarkup,
    )
from telegram.ext import (
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    CallbackContext,
    filters,
)
from telegram.constants import ParseMode, ChatType

from django.utils import timezone
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
from server.logger import logger

from tarot.utils.flaresolverr import tarot_fetch

from tarot.bot.allcard_handler import AllCardHandler
from tarot.bot.ai_interpret_handler import AIInterpretHandler
from tarot.bot.rune_handler import RuneHandler
from tarot.bot.meaning_handler import MeaningHandler
from tarot.bot.cards_handler import CardsHandler
from tarot.bot.canvas_handler import CanvasHandler
from tarot.bot.oh_handler import OhHandler
from tarot.bot.settings_handler import SettingsHandler

from tarot.messages import CardMessages
from tarot.messages import CANVAS_3_TRIGGER, TAROT_3_TRIGGER, ONEHAND_TRIGGER

from tarot.utils.redis_client import (
    redis_client,
    redis_client_bot,
    REDIS_TTL_SECONDS,
    REDIS_KEY_TEMPLATE,
)

CATEGORY_ICONS = {
    UserReading.ReadingCategory.ONE: "🎴",
    UserReading.ReadingCategory.TAROT: "🔮",
    UserReading.ReadingCategory.ORACLE: "✨",
    UserReading.ReadingCategory.RUNES: "🪨",
    UserReading.ReadingCategory.CANVAS_SPREAD: "🖼️",
    UserReading.ReadingCategory.TAROT_STICKER: "🏷️",
    UserReading.ReadingCategory.ALL: "🃏",
}
CATEGORY_COMMANDS = {
    UserReading.ReadingCategory.ONE: "/one",
    UserReading.ReadingCategory.TAROT: "/card",
    UserReading.ReadingCategory.ORACLE: "/oraculum",
    UserReading.ReadingCategory.RUNES: "/futhark",
    UserReading.ReadingCategory.CANVAS_SPREAD: "/canvas",
    UserReading.ReadingCategory.TAROT_STICKER: "/tarot",
    UserReading.ReadingCategory.ALL: "/all",
}

LAST_READINGS_MAX_DAYS = 7      # за неделю
LAST_READINGS_MAX_TOTAL = 100   # не более 100 записей


class TarotBot(AbstractBot):
    def __init__(self):
        self.allcard_handler = AllCardHandler(self)
        self.ai_interpret_handler = AIInterpretHandler(self)
        self.rune_handler = RuneHandler(self)
        self.meaning_handler = MeaningHandler(self)
        self.cards_handler = CardsHandler(self)
        self.canvas_handler = CanvasHandler(self)
        self.oh_handler = OhHandler(self)
        self.settings_handler = SettingsHandler(self)
        self.messages = CardMessages()
        self.handlers = self.get_handlers()

    def get_handlers(self):
        return [
            MessageHandler(filters.PHOTO, self.handle_photo_msg),
            CommandHandler("start", self.handle_start, filters.ChatType.PRIVATE),
            CommandHandler("help", self.handle_help, filters.ChatType.PRIVATE),
            
            *self.allcard_handler.get_handlers(),
            *self.rune_handler.get_handlers(),
            *self.ai_interpret_handler.get_handlers(),
            *self.meaning_handler.get_handlers(),
            *self.cards_handler.get_handlers(),
            *self.canvas_handler.get_handlers(),
            *self.oh_handler.get_handlers(),
            *self.settings_handler.get_handlers(),
            
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
            CallbackQueryHandler(
                self.handle_last_page,
                pattern=r"^lastpage_\d+$",
            ),
            
            CommandHandler("one", self.handle_one_command, filters.ChatType.PRIVATE),
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
        **kwargs,
    ):
        # Извлекаем все параметры из kwargs с дефолтами
        text = kwargs.pop("text", "")
        category = kwargs.pop("category", "tarot")
        initial_count = kwargs.pop("count", 1)
        deck_id = kwargs.pop("deck_id", None)
        is_flipped_allowed = kwargs.pop("is_flipped_allowed", False)
        is_major_only = kwargs.pop("is_major_only", False)
        card_ids = kwargs.pop("card_ids", None)
        original_query = kwargs.pop("original_query", "")
        is_command = kwargs.pop("is_command", True)
        original_message_text = kwargs.pop("original_message_text", "")
        
        # Защита от пустых значений для JSONField
        if card_ids is None:
            card_ids = []

        # Создаем запись
        reading = await UserReading.objects.acreate(
            bot_id=self.app_bot_id,
            user=user,
            category=category,
            initial_count=initial_count,
            count=initial_count,
            deck_id=deck_id,
            is_flipped_allowed=is_flipped_allowed,
            is_major_only=is_major_only,
            text=text,
            message_id=message_id,
            card_ids=card_ids, 
            original_query=original_query,
            is_command=is_command,
            original_message_text=original_message_text,
        )
        logger.info(f"Результат гадания сохранен: {reading}")

        # Сохраняем отметку в Redis
        try:
            redis_key = REDIS_KEY_TEMPLATE.format(
                user_id=user.tg_id, 
                category=category, 
                app_id=self.app_bot_id
            )
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
        # === 0. Высекаем @username бота, если есть ===
        msg_text = re.sub(r"@\w+", "", msg_text)
        
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
            
            options["card_ids"] = card_ids  # Берём ровно то, что запросили
            logger.info(f"Парсинг ID карт: запрошено={len(card_ids)}, card_ids={options['card_ids']}")

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
            remaining = remaining[:counter_match.start()] + remaining[counter_match.end():]
            remaining = re.sub(r"\s+", " ", remaining).strip()
        options["counter"] = counter
        
        # 2. Парсинг флага перевернутых позиций
        options["flip"] = False
        flip_match = re.search(r"(?<!\w)(переверн\w*|flip)(?!\w)", remaining, flags=re.IGNORECASE)
        if flip_match:
            options["flip"] = True
            remaining = remaining[:flip_match.start()] + remaining[flip_match.end():]
            remaining = re.sub(r"\s+", " ", remaining).strip()
        
        # 3. Парсинг флага Старших Арканов
        options["major"] = False
        major_match = re.search(r"(?<!\w)(старш\w*|major)(?!\w)", remaining, flags=re.IGNORECASE)
        if major_match:
            options["major"] = True
            remaining = remaining[:major_match.start()] + remaining[major_match.end():]
            remaining = re.sub(r"\s+", " ", remaining).strip()
        
        # 4. Парсинг колоды
        options["deck"] = None
        options["deck_keyword"] = None
        options["original_query"] = ""
        
        deck_match = re.search(r"(?<!\w)(колода|deck)\s+(.+)", remaining, flags=re.IGNORECASE)
        if deck_match:
            deck_value = deck_match.group(2).strip()
            deck_value = deck_value.strip('"\"\'')
            
            if deck_value.isdigit():
                options["deck"] = int(deck_value)
            else:
                options["deck_keyword"] = deck_value
            
            original_query = remaining[:deck_match.start()].strip()
            options["original_query"] = original_query if original_query else ""
        else:
            remaining = remaining.strip()
            if remaining:
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
        app_id = self.app_bot_id
        # Формируем ключ по тому же шаблону, что и при сохранении
        redis_key = REDIS_KEY_TEMPLATE.format(user_id=user_id, category=category, app_id=app_id)
        # Ключ для хранения ID сообщения кулдауна
        msg_ttl_key = f"user:ttl:message:{user_id}:{category}:{app_id}"

        try:
            # Запрашиваем оставшееся время жизни ключа (в секундах)
            time_left = await redis_client.ttl(redis_key)

            # Redis возвращает:
            # -1, если ключ существует, но у него нет TTL (бессрочный)
            # -2, если ключа нет в базе (кулдауна нет, можно гадать)
            if time_left > 0:
                is_group = update.effective_chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
                if is_group:
                    try:
                        await update.effective_message.delete()
                    except Exception:
                        pass  # нет прав на удаление
                    return True
                
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
                
                for cat_choice in UserReading.ReadingCategory.values:
                    if cat_choice == category:
                        continue  # Пропускаем текущую заблокированную категорию

                    # Формируем ключ для проверки
                    check_key = REDIS_KEY_TEMPLATE.format(user_id=user_id, category=cat_choice, app_id=app_id)
                    check_ttl = await redis_client.ttl(check_key)

                    # Если ключа нет (time_left == -2) - категория доступна
                    if check_ttl == -2:
                        command = CATEGORY_COMMANDS.get(cat_choice)   # ← глобальная константа
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
            logging.error(f"Ошибка проверки TTL в Redis: {e}", exc_info=True)

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
            decks = model.objects.annotate(
                similarity=(
                    TrigramSimilarity('name', deck_keyword) + 
                    TrigramSimilarity('slug', deck_keyword)
                )
            ).filter(
                Q(name__icontains=deck_keyword) |
                Q(slug__icontains=deck_keyword) |
                Q(seo_tags__icontains=deck_keyword) |
                Q(similarity__gt=0.3)  # ← Порог похожести для опечаток
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

    async def format_card_name(self, card, text_join='\n'):
        instance = card.get("card_instance")
        flipped = card.get("flipped", False)

        # Основное описание (название или описание из модели)
        main_desc = ""
        if isinstance(instance, OraculumItem):
            main_desc = (instance.description or "")
        elif isinstance(instance, TarotCardItem):
            main_desc = (instance.custom_description or "")

        # Текст значения (прямое или перевернутое)
        value_text = ""
        if isinstance(instance, OraculumItem):
            if flipped and instance.inverted:
                value_text = f"Перевернуто: {instance.inverted}"
            else:
                value_text = instance.direct or ""

        # Форматируем имя карты через Messages (с ⬇️ вместо "Перевернуто")
        formatted_name = self.messages.format_card_name(instance.display_name, flipped)

        # Собираем все части
        parts = [
            formatted_name,
            " ".join([s.strip() for s in [main_desc, value_text]])
        ]

        return text_join.join(str(p) for p in parts if p)

    async def handle_one_command(self, update: Update, context: CallbackContext):
        """
        Обработчик /one — мгновенно возвращает управление боту,
        вся работа выполняется в фоновой задаче.
        """
        category = UserReading.ReadingCategory.ONE
        is_locked = await self.check_reading_cooldown(update, category)
        if is_locked:
            return

        asyncio.create_task(self._handle_one_background(update, context))

    async def _handle_one_background(self, update: Update, context: CallbackContext):
        """
        Фоновая задача с полной логикой /one.
        Выполняется параллельно с обработкой других сообщений ботом.
        """
        category = UserReading.ReadingCategory.ONE
        reading = None
        tech_msg = None
        try:
            user = await self.get_or_create_tg_user(update)
            
            reading = await self.save_reading(
                user=user,
                message_id=update.effective_message.message_id,
                category=category,
                count=1,
                is_command=True,
                original_message_text=update.effective_message.text or "",
            )
            reading.reading_status = UserReading.ReadingStatus.PENDING
            await reading.asave()

            tarot_url = "https://www.tarot.com"
            decks_url = "/tarot/decks"

            tech_msg = await update.effective_message.reply_text(
                "Выбираю колоду", reply_to_message_id=update.effective_message.message_id
            )
            tech_msg_id = tech_msg.message_id

            # 🔥 Обход Cloudflare через FlareSolverr
            content = await tarot_fetch(f"{tarot_url}{decks_url}")
            dom = BeautifulSoup(content, "html.parser")

            decks_raw = dom.select(".tarot-deck-list a")
            decks = [el["href"] for el in decks_raw if el.get("href")]

            random_deck_id = random.randint(0, len(decks) - 1)
            random_deck = decks[random_deck_id]

            await context.bot.edit_message_text(
                "Выбираю карту",
                chat_id=update.effective_chat.id,
                message_id=tech_msg_id,
            )

            # 🔥 И здесь тоже
            content = await tarot_fetch(f"{tarot_url}{random_deck}")
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

            result_text = f"{random_card['name']}\n{random_card['url']}"

            reading.text = result_text
            reading.reading_status = UserReading.ReadingStatus.SUCCESS
            await reading.asave()

            await update.effective_message.reply_photo(
                random_card["img"],
                result_text,
                reply_to_message_id=update.effective_message.message_id,
            )
            await context.bot.delete_message(update.effective_chat.id, tech_msg_id)

        except Exception as e:
            logger.error(f"Ошибка в handle_one_command: {e}", exc_info=True)
            if reading:
                reading.reading_status = UserReading.ReadingStatus.ERROR
                await reading.asave()
            try:
                if tech_msg:
                    await context.bot.delete_message(update.effective_chat.id, tech_msg.message_id)
            except:
                pass
        
    async def _build_last_readings_page(
        self,
        user: TgUser,
        offset: int = 0,
        limit: int = 5,
    ):
        week_ago = timezone.now() - timedelta(days=7)
        base_qs = (
            UserReading.objects.filter(user=user, created_at__gte=week_ago)
            .order_by("-created_at")
        )
        total_count = min(await base_qs.acount(), 100)

        if offset >= total_count:
            offset = 0

        readings_qs = base_qs[offset : offset + limit + 1]

        readings = []
        async for item in readings_qs:
            formatted_date = item.created_at.strftime("%d.%m.%Y %H:%M")
            icon = CATEGORY_ICONS.get(item.category, "🔮")
            command = CATEGORY_COMMANDS.get(item.category, "")

            if item.count > 1:
                if item.category == UserReading.ReadingCategory.RUNES and item.count == 3:
                    command += "_triplet"
                else:
                    command += str(item.count)
            if item.is_flipped_allowed:
                command += "_flip"
            if item.is_major_only and item.category in (
                UserReading.ReadingCategory.TAROT,
                UserReading.ReadingCategory.CANVAS_SPREAD,
                UserReading.ReadingCategory.ALL,
            ):
                command += "_major"

            # --- Таро, Canvas, All ---
            if item.category in (
                UserReading.ReadingCategory.TAROT,
                UserReading.ReadingCategory.CANVAS_SPREAD,
                UserReading.ReadingCategory.ALL,
            ):
                cards_lines = []
                elements = item.card_ids[:item.count] if item.card_ids else []
                card_ids = [int(el['id']) for el in elements if isinstance(el, dict)]

                cards_map = {
                    c.id: c
                    async for c in TarotCardItem.objects.select_related('tarot_card').filter(id__in=card_ids)
                }

                for element in elements:
                    if not isinstance(element, dict):
                        continue
                    card_item = cards_map.get(int(element['id']))
                    if card_item:
                        cards_lines.append(self.messages.format_card_name(card_item.display_name, element.get('flip', False)))
                    else:
                        cards_lines.append(f"❓ #{element['id']}")

                try:
                    deck = await TarotDeck.objects.aget(id=item.deck_id) if item.deck_id else None
                    deck_name = deck.name if deck else "неизвестная колода"
                except ObjectDoesNotExist:
                    deck_name = "неизвестная колода"

                safe_text = ', '.join(cards_lines) + f" из колоды {deck_name}"

            # --- Оракул ---
            elif item.category == UserReading.ReadingCategory.ORACLE:
                cards_lines = []
                elements = item.card_ids if item.card_ids else []
                card_ids = [int(el['id']) for el in elements if isinstance(el, dict)]

                cards_map = {
                    c.id: c
                    async for c in OraculumItem.objects.select_related('deck').filter(id__in=card_ids)
                }

                for element in elements:
                    if not isinstance(element, dict):
                        continue
                    card_item = cards_map.get(int(element['id']))
                    if card_item:
                        cards_lines.append(self.messages.format_card_name(card_item.display_name, element.get('flip', False)))
                    else:
                        cards_lines.append(f"❓ #{element['id']}")

                try:
                    deck = await OraculumDeck.objects.aget(id=item.deck_id) if item.deck_id else None
                    deck_name = deck.name if deck else "неизвестная колода"
                except ObjectDoesNotExist:
                    deck_name = "неизвестная колода"

                safe_text = ', '.join(cards_lines) + f" из колоды {deck_name}"

            else:
                safe_text = item.text[:200].replace("<", "&lt;").replace(">", "&gt;")

            readings.append(f"📅 {formatted_date} {icon} {command}\n{safe_text}\n")

        has_next = (offset + limit) < total_count
        readings = readings[:limit]

        if not readings:
            return None, None, False

        current_page = (offset // limit) + 1
        total_pages = max(1, (total_count + limit - 1) // limit)

        text = f"📜 <b>Ваши гадания за 7 дней (стр. {current_page}/{total_pages}):</b>\n\n"
        text += "\n".join(readings)

        if total_count >= 100:
            text += f"\n<i>Показаны последние 100 записей.</i>"

        keyboard = []
        if offset > 0:
            prev_offset = offset - limit
            prev_page = current_page - 1
            keyboard.append(
                InlineKeyboardButton(
                    text=f"⬅️ {prev_page}/{total_pages}",   # ← номер страницы
                    callback_data=f"lastpage_{prev_offset}",  # ← offset в callback
                )
            )
        if has_next:
            next_offset = offset + limit
            next_page = current_page + 1
            keyboard.append(
                InlineKeyboardButton(
                    text=f"{next_page}/{total_pages} ➡️",    # ← номер страницы
                    callback_data=f"lastpage_{next_offset}",   # ← offset в callback
                )
            )

        markup = InlineKeyboardMarkup([keyboard]) if keyboard else None
        return text, markup, has_next

    async def handle_last_page(self, update: Update, context: CallbackContext):
        query = update.callback_query

        try:
            _, offset_str = query.data.split("_")
            new_offset = int(offset_str)

            # === ЗАЩИТА: не переключаемся на ту же страницу ===
            current_text = query.message.text or ""
            import re
            match = re.search(r"\(стр\. (\d+)/(\d+)\)", current_text)
            if match:
                current_page = int(match.group(1))
                current_offset = (current_page - 1) * 5  # limit = 5
                if new_offset == current_offset:
                    await query.answer("Вы уже на этой странице")
                    return

            await query.answer()

            user = await self.get_or_create_tg_user(update)
            if not user:
                return

            text, keyboard, _ = await self._build_last_readings_page(user, offset=new_offset)

            if text is None:
                await query.edit_message_text("Гаданий не найдено.")
                return

            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )

        except Exception as e:
            logger.error(f"Ошибка при переключении страницы истории: {e}", exc_info=True)
            try:
                await query.edit_message_text(
                    "Произошла ошибка при загрузке страницы. Попробуйте снова."
                )
            except Exception:
                pass

    async def handle_last_readings(self, update: Update, context: CallbackContext):
        """
        Обработчик команды /last — первая страница.
        """
        logger.info(f"Запрос истории гаданий для пользователя: {update.effective_user.id}")

        try:
            user = await self.get_or_create_tg_user(update)
            if not user:
                return

            text, keyboard, _ = await self._build_last_readings_page(user, offset=0)

            if text is None:
                await update.effective_message.reply_text("Гаданий не найдено.")
                return

            await update.effective_message.reply_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                reply_to_message_id=update.effective_message.message_id,
                disable_web_page_preview=True,
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
            command = self.messages.build_deck_command(f"/{command_name}", deck.slug)
            decks_text.append(
                self.messages.get_deck_list_item(command, deck.name)
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
            logger.info(f"Обработка команды /decks с текстом: {msg_text[:100]}")

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
            
    async def handle_photo_msg(self, update: Update, context: CallbackContext):
        logger.info(update)

    def default_reply_keyboard(self):
        return ReplyKeyboardMarkup(
            [[TAROT_3_TRIGGER, CANVAS_3_TRIGGER], [ONEHAND_TRIGGER]],
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
