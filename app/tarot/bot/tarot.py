import re
import os
from typing import List, Optional, Dict

import json
import redis.asyncio as aioredis
import aiohttp
import random
from io import BytesIO
from bs4 import BeautifulSoup

from telegram import Update, InputMediaPhoto, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    CallbackContext,
    filters,
)
from telegram.constants import ParseMode

from django.utils.timezone import now, timedelta
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
    Rune,
    UserReading
)
from tg_bot.models import BotFileCache
from server.logger import logger
from django.conf import settings

from tarot.utils.image_utils import create_spread_image

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
        self.handlers = self.get_handlers()

    def get_handlers(self):
        return [
            MessageHandler(filters.PHOTO, self.handle_photo_msg),
            CommandHandler("start", self.handle_help),
            CommandHandler("help", self.handle_help),
            MessageHandler(
                filters.COMMAND
                & filters.TEXT
                & filters.ChatType.PRIVATE
                & filters.Regex(r"^\/all deck \d+$"),
                self.handle_all_by_deck,
            ),
            CallbackQueryHandler(
                self.handle_allcard_callback,
                pattern=r"^allcard_",
            ),
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
            CommandHandler(["onehand", "oh"], self.handle_onehand, filters.ChatType.PRIVATE),
            CallbackQueryHandler(self.handle_onehand_callback, pattern=r"^oh_"),
            CommandHandler("one", self.handle_one_command, filters.ChatType.PRIVATE),
            MessageHandler(
                filters.COMMAND
                & filters.TEXT
                & filters.ChatType.PRIVATE
                & filters.Regex(r"^\/card(\d+)?"),
                self.handle_card,
            ),
            CallbackQueryHandler(self.handle_more_button, pattern=r"^more_\d+_.+$"),
            CallbackQueryHandler(
                self.handle_desc_button, pattern=r"^desc_(\d+(?:#\d+)*)$"
            ),
            CallbackQueryHandler(
                self.handle_pagination, pattern=r"^meaning_[a-z0-9]+_\d+_[0-9#]+_\d+$"
            ),
            MessageHandler(
                filters.COMMAND
                & filters.TEXT
                & filters.ChatType.PRIVATE
                & filters.Regex(r"^\/oraculum(\d+)?"),
                self.handle_oraculum,
            ),
            CallbackQueryHandler(
                self.handle_moreoracle_button, pattern=r"^moreoracle_\d+_.+$"
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
    ):
        # 1. Защита от пустых значений для JSONField
        if card_ids is None:
            card_ids = []

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

    async def format_card_name(self, card):
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
        
        return "\n".join(str(p) for p in parts if p)

    async def send_card(self, update: Update, cards, meaning_cards, deck, flip, major):
        logger.info(
            (f"Отправка значений  и клавиатуры: meaning_cards={meaning_cards}, deck={deck}, cards_count={len(cards)},"
                f"flip={flip}, major={major}")
            )
        await update.effective_message.reply_media_group(
            [
                InputMediaPhoto(
                    (c["img_id"]),
                    await self.format_card_name(c),
                )
                for c in cards
            ],
            reply_to_message_id=update.effective_message.message_id,
        )

        text = [deck.name]
        reply_markup = [
            [
                InlineKeyboardButton(
                    "Еще карту",
                    callback_data=f"moreoracle_{deck.id}_{int(major)}_{int(flip)}",
                )
            ]
        ]
        if isinstance(deck, TarotDeck):
            text.append(deck.link)
            reply_markup = [
                [
                    InlineKeyboardButton(
                        "Еще карту",
                        callback_data=f"more_{deck.id}_{int(major)}_{int(flip)}",
                    ),
                    InlineKeyboardButton(
                        f"Базовые значения (для {len(meaning_cards)} карт)",
                        callback_data=f"desc_{'#'.join(meaning_cards)}",
                    ),
                ]
            ]
        reply_id = update.effective_message.message_id
        if update.effective_message.reply_to_message:
            reply_id = update.effective_message.reply_to_message.message_id
            
        params = {
            "text": "\n".join(text),
            "reply_to_message_id": reply_id,
            "disable_web_page_preview": True,
        }
        if reply_markup:
            params["reply_markup"] = InlineKeyboardMarkup(reply_markup)

        await update.effective_message.reply_text(**params)

    def parse_reading_options(self, msg_text: str) -> dict:
        """
        Полный парсинг аргументов команды из текста сообщения.
        Поддерживает: /card3, deck 5, flip, major, c12_15_23
        """
        msg_lower = msg_text.lower()
        options = {}

        # 1. Парсинг количества карт/рун (/card3, /oraculum6)
        counter_found = re.search(r"/[a-zA-Z]+(\d+)", msg_lower)
        options["counter"] = int(counter_found.group(1)) if counter_found else 1
        options["counter"] = max(1, min(options["counter"], 10))

        # 2. Парсинг ID колоды (deck 5)
        deck_found = re.search(r"deck\s*(\d+)", msg_lower)
        options["deck"] = int(deck_found.group(1)) if deck_found else None

        # 3. Парсинг флага перевернутых позиций (flip)
        options["flip"] = "flip" in msg_lower

        # 4. Парсинг флага Старших Арканов (major)
        options["major"] = "major" in msg_lower

        # 5. Парсинг конкретных ID карт (формат: c12_15_23)
        # Ищем в msg_text (оригинальном, на случай если регистр c/C важен, хотя регулярка покроет)
        card_ids_found = re.findall(r"[cC](\d+(?:_\d+)*)", msg_text)
        
        if card_ids_found:
            # Вытаскиваем числа из первой найденной группы, делим по модулю 78
            card_ids = [int(c) % 78 for c in card_ids_found[0].split("_")]
            
            # Если переданных ID меньше, чем заказано в counter, циклически инкрементируем последний ID
            target_count = options["counter"]
            if len(card_ids) < target_count:
                temp_id = card_ids[-1]
                for _ in range(len(card_ids), target_count):
                    temp_id = (temp_id + 1) % 78
                    card_ids.append(temp_id)
            
            # Отрезаем лишнее, если передали больше, чем counter
            options["card_ids"] = card_ids[:target_count]
            logger.info(f"Парсинг ID карт: card_ids={options['card_ids']}")
        else:
            options["card_ids"] = None
            logger.info("ID карт не указаны, будут выбраны случайные карты.")

        logger.info(
            f"Опции расклада полностью собраны: counter={options['counter']}, "
            f"deck={options['deck']}, flip={options['flip']}, "
            f"major={options['major']}, has_custom_ids={options['card_ids'] is not None}"
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
        # Если шаблон не в классе, можно использовать строку: f"user:{user_id}:{category}"
        redis_key = REDIS_KEY_TEMPLATE.format(user_id=user_id, category=category)
        
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
                    
                await update.effective_message.reply_text(message)
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
        is_locked = await self.check_reading_cooldown(update, category)
        if is_locked:
            return
        
        try:
            options = self.parse_reading_options(msg_text)

            # Получение колоды
            deck = await self.get_deck(options.get("deck"))
            logger.info(f"Используемая колода: {deck.id if deck else 'не указана'}")

            # Получение карт
            cards = await self.get_cards(
                deck.id if deck else None,
                options.get("counter", 1),
                options.get("card_ids"),
                options.get("major", False),
                options.get('flip')
            )
            user = await self.get_or_create_tg_user(update)
            await self.save_reading(
                user=user,
                message_id=update.effective_message.message_id,
                text=f"{deck.name if deck else 'Дефолтная колода'}: " + ", ".join(
                    [await self.format_card_name(c) for c in cards]
                ),
                category=category,
                count=options.get("counter", 1),
                deck_id=deck.id if deck else None,
                is_flipped_allowed=options.get('flip', False),
                is_major_only=options.get('major', False),
                card_ids=[c["card_id"] for c in cards]
            )

            # Отправка карт
            await self.send_card(
                update,
                cards,
                [c["card_id"] for c in cards],
                deck,
                options["flip"],
                options['major']
            )
            logger.info(
                f"Карта отправлена: {[str(n.get(k)) for k in ['card_id', 'name'] for n in cards]}"
            )

        except Exception as e:
            logger.error(f"Ошибка при обработке команды /card: {e}", exc_info=True)
            await update.message.reply_text(
                "Произошла ошибка при обработке вашего запроса. Пожалуйста, попробуйте снова."
            )

    async def handle_oraculum(self, update: Update, context: CallbackContext):
        msg_text = update.message.text
        logger.info(f"Обработка команды /oraculum с текстом: {msg_text[:100]}")
        
        category = UserReading.ReadingCategory.ORACLE
        is_locked = await self.check_reading_cooldown(update, category)
        if is_locked:
            return
        
        try:
            options = self.parse_reading_options(msg_text)

            deck = await self.get_deck(options.get("deck"), "oraculum")
            logger.info(f"Используемая колода: {deck.id if deck else 'не указана'}")

            # Получение карт
            cards = await self.get_oraculum_cards(
                deck.id if deck else None, 
                options.get("counter", 1), 
                [],
                options['flip']
            )
            logger.info(f"Получено карт: {len(cards)}")
            user = await self.get_or_create_tg_user(update)
            await self.save_reading(
                user=user,
                message_id=update.effective_message.message_id,
                text=f"{deck.name if deck else 'Дефолтный оракул'}: " + ", ".join(
                    [await self.format_card_name(c) for c in cards]
                ),
                category=category,
                count=options.get("counter", 1),
                deck_id=deck.id if deck else None,
                is_flipped_allowed=options.get('flip', False),
                is_major_only=False,  
                card_ids=[c["card_id"] for c in cards]
            )

            # Отправка карт
            await self.send_card(
                update,
                cards,
                [],
                deck,
                options["flip"],
                False
            )
            logger.info(
                f"Карта отправлена: {[str(n.get(k)) for k in ['card_id', 'name'] for n in cards]}"
            )

        except Exception as e:
            logger.error(f"Ошибка при обработке команды /oraculum: {e}", exc_info=True)
            await update.message.reply_text(
                "Произошла ошибка при обработке вашего запроса. Пожалуйста, попробуйте снова."
            )

    async def handle_moreoracle_button(self, update: Update, context: CallbackContext):
        query = update.callback_query
        await query.answer()
        logger.info(f"Получен callback-запрос: {query.data}")

        # === ШАГ 1: Разбираем входящие данные ===
        try:
            _, deck_id, major, flip = query.data.split("_")
            major = int(major)
            flip = int(flip)
            deck_id_int = int(deck_id)
            deck = await self.get_deck(deck_id_int, "oraculum")
            logger.info(f"Выражение {query.data} разобрано. Загружена колода Оракула: {deck}")

        except ValueError as e:
            logger.error(f"Ошибка при разборе query.data: {query.data}, ошибка: {e}")
            await query.edit_message_text("Ошибка обработки запроса.")
            return
        
        except Exception as e:
            logger.error(f"Ошибка загрузки колоды {deck_id}: {e}")
            await query.edit_message_text("Ошибка загрузки колоды.")
            return
        

        # Находим пользователя и исходный ID сообщения
        user = await self.get_or_create_tg_user(update)
        if not update.effective_message.reply_to_message:
            logger.warning("Не найдено исходное сообщение reply_to_message для добора.")
            initial_message_id = update.effective_message.message_id
        else:
            initial_message_id = update.effective_message.reply_to_message.message_id

        try:
            # === ШАГ 2: Получаем или создаем прошлую запись в БД ===
            # Мы делаем это ДО генерации карты, чтобы узнать, что выпадало ранее
            reading, created = await UserReading.objects.aget_or_create(
                message_id=initial_message_id,
                user=user,
                defaults={
                    "category": UserReading.ReadingCategory.ORACLE,
                    "deck_id": deck.id if deck else None,
                    "text": f"{deck.name if deck else 'Оракул'}: ",
                    "count": 0,
                    "card_ids": []
                }
            )

            # === ШАГ 3: Собираем список исключений из базы ===
            exclude_cards = [str(cid) for cid in (reading.card_ids or [])]
            logger.info(f"Исключаем уже выпавшие карты (из БД): {exclude_cards}")

            # Извиняюсь, вызываем генератор карт:
            new_card = await self.get_oraculum_cards( 
                deck_id=deck_id_int,
                counter=1,
                exclude_cards=exclude_cards,
                flip=flip
            )

            if not new_card:
                logger.info("Нет доступных карт для выбора.")
                await update.effective_message.reply_text("Нет доступных карт для выбора (колода закончилась).")
                await update.effective_message.edit_reply_markup(reply_markup=InlineKeyboardMarkup([]))
                return

            # Форматируем имя новой карты для текста
            new_card_text = ", ".join([await self.format_card_name(c) for c in new_card])
            new_card_ids = [c["card_id"] for c in new_card]

            # === ШАГ 4: Обновляем запись в БД новыми данными ===
            if created:
                # Если запись была пустой (создалась только что в defaults)
                reading.text = f"{deck.name if deck else 'Оракул'}: {new_card_text}"
                reading.count = 1
                reading.card_ids = new_card_ids
            else:
                # Если запись уже существовала — дописываем текст и ID карт
                reading.text = f"{reading.text}, {new_card_text}"
                reading.count += 1
                
                current_ids = reading.card_ids or []
                current_ids.extend(new_card_ids)
                reading.card_ids = current_ids

            # Сохраняем обновленный лог в базу
            await reading.asave()

            # Отправляем карту пользователю в чат
            await self.send_card(
                update,
                new_card,
                new_card_ids,
                deck,
                bool(major),
                bool(flip),
            )
            logger.info(f"Карта добора успешно отправлена: {new_card_text[:100]}")

        except Exception as e:
            logger.error(f"Ошибка при обработке добора карты: {e}", exc_info=True)
            await query.edit_message_text("Ошибка при обработке добора карты.")
            
    async def handle_more_button(self, update: Update, context: CallbackContext):
        query = update.callback_query
        await query.answer()
        logger.info(f"Получен callback-запрос: {query.data}")

        # === ШАГ 1: Разбираем входящие данные и сразу грузим колоду ===
        try:
            _, deck_id, major, flip = query.data.split("_")
            major = int(major)
            flip = int(flip)
            deck_id_int = int(deck_id)
            deck = await self.get_deck(deck_id_int)
            logger.info(f"Выражение {query.data} разобрано. Загружена колода Таро: {deck}")
            
        except ValueError as e:
            logger.error(f"Ошибка при разборе query.data: {query.data}, ошибка: {e}")
            await query.edit_message_text("Ошибка обработки запроса.")
            return
        except Exception as e:
            logger.error(f"Ошибка загрузки колоды {deck_id}: {e}")
            await query.edit_message_text("Ошибка загрузки колоды.")
            return

        # Находим пользователя и исходный ID сообщения
        user = await self.get_or_create_tg_user(update)
        if not update.effective_message.reply_to_message:
            logger.warning("Не найдено исходное сообщение reply_to_message для добора.")
            initial_message_id = update.effective_message.message_id
        else:
            initial_message_id = update.effective_message.reply_to_message.message_id

        try:
            # === ШАГ 2: Получаем или создаем прошлую запись в БД ===
            reading, created = await UserReading.objects.aget_or_create(
                message_id=initial_message_id,
                user=user,
                defaults={
                    "category": UserReading.ReadingCategory.TAROT,
                    "deck_id": deck.id if deck else None,
                    "text": f"{deck.name if deck else 'Таро'}: ",
                    "count": 0,
                    "card_ids": []
                }
            )

            # === ШАГ 3: Берем список исключений СТРОГО из базы данных ===
            exclude_cards = [str(cid) for cid in (reading.card_ids or [])]
            logger.info(f"Получаем карты с major {bool(major)} flip {bool(flip)}. Исключаем из БД: {exclude_cards}")

            # Вызываем генератор карт Таро
            new_card = await self.get_cards(
                deck_id=deck_id_int,
                counter=1,
                card_ids=None,
                exclude_cards=exclude_cards,
                major=bool(major),
                flip=bool(flip)
            )

            if not new_card:
                logger.info("Нет доступных карт для выбора.")
                await update.effective_message.reply_text("Нет доступных карт для выбора (колода закончилась).")
                await update.effective_message.edit_reply_markup(reply_markup=InlineKeyboardMarkup([]))
                return

            # Форматируем имя новой карты для текста
            new_card_text = ", ".join([await self.format_card_name(c) for c in new_card])
            new_card_ids = [c["card_id"] for c in new_card]
            logger.info(f"Выбраны новые карты: {[c['name'] + ' ' + str(c['flipped']) for c in new_card]}")

            # === ШАГ 4: Обновляем запись в БД новыми данными ===
            if created:
                # Если запись была создана в defaults с нуля
                reading.text = f"{deck.name if deck else 'Таро'}: {new_card_text}"
                reading.count = 1
                reading.card_ids = new_card_ids
            else:
                # Если запись уже существовала — дописываем текст, инкрементируем счетчик и дополняем ID
                reading.text = f"{reading.text}, {new_card_text}"
                reading.count += 1
                
                current_ids = reading.card_ids or []
                current_ids.extend(new_card_ids)
                reading.card_ids = current_ids

            # Сохраняем обновленный лог в базу
            await reading.asave()
            logger.info(f"Запись Таро {reading.id} успешно обновлена в БД. Карты: {reading.card_ids}")

            # Отправляем карту пользователю в чат
            # Обрати внимание на порядок аргументов bool(flip) и bool(major), как было в твоем исходнике
            await self.send_card(
                update,
                new_card,
                new_card_ids,
                deck,
                bool(flip),
                bool(major),
            )
            logger.info(f"Карта добора Таро успешно отправлена: {new_card_text}")

        except Exception as e:
            logger.error(f"Ошибка при обработке добора карты Таро: {e}", exc_info=True)
            await query.edit_message_text("Ошибка при обработке добора карты.")
            
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

    async def load_page(self, url):
        async with aiohttp.ClientSession() as session:
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

    async def handle_futark(self, update: Update, context: CallbackContext):
        """
        Обработчик команды /futark.
        """
        msg_text = update.message.text
        user = await self.get_or_create_tg_user(update)
        logger.info(f"Обработка команды /futark с текстом: {msg_text[:100]}")
        
        category = UserReading.ReadingCategory.RUNES
        is_locked = await self.check_reading_cooldown(update, category)
        if is_locked:
            return

        try:
            # Получаем все руны из базы данных
            runes = [rune async for rune in Rune.objects.all()]

            if "triplet" in msg_text.lower():
                # Выбираем 3 случайные руны
                selected_runes = random.sample(runes, 3)
                rune_texts = []
                keyboard = []

                for i, rune in enumerate(selected_runes):
                    inverted = random.choice(
                        [True, False]
                    )  # Случайно определяем, перевернута ли руна
                    rune_texts.append(
                        f"<b>{rune.symbol}</b> {rune.type}{' (Перевернутая)' if inverted else ''}"
                    )
                    keyboard.append(
                        InlineKeyboardButton(
                            text=f"{rune.symbol} {rune.type}{' 🔄' if inverted else ''}",
                            callback_data=f"futhark_{rune.id}_{int(bool(inverted))}_{i + 1}",
                        )
                    )
                await self.save_reading(
                    user=user,
                    message_id=update.effective_message.message_id,
                    text=" ".join(rune_texts),
                    category=category,
                    is_flipped_allowed=True,
                    count=3
                )

                # Отправляем сообщение с рунами и inline-клавиатурой
                await update.message.reply_text(
                    "\n".join(rune_texts),
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([keyboard]),
                )
            else:
                # Выбираем одну случайную руну
                random_rune = random.choice(runes)
                inverted = "flip" in msg_text.lower() and random.choice([True, False])
                
                # Формируем текст сообщения
                text_parts = [f"<b>{random_rune.symbol}</b>", random_rune.type]
                if inverted:
                    text_parts.append("Перевернуто")

                await self.save_reading(
                    user=user,
                    message_id=update.effective_message.message_id,
                    text=" ".join(text_parts),
                    category=UserReading.ReadingCategory.RUNES,
                    is_flipped_allowed=True,
                    count=1
                )

                # Отправляем текст и стикер
                await update.message.reply_text(
                    "\n".join(text_parts), parse_mode="HTML"
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

        try:
            # Парсим callback_data
            callback_data = query.data
            rune_id, inverted, position = callback_data.split("_")[1:]

            inverted = bool(int(inverted))
            position = int(position)

            logger.info(
                f"Обработка футарк колбэка {callback_data}: руна {rune_id}, перевернуто {inverted} на позиции {position}"
            )

            # Получаем описание руны
            rune = await Rune.objects.aget(id=rune_id)

            if inverted == False:
                keys = rune.straight_keys
                meaning = rune.straight_meaning
                pos_1 = rune.straight_pos_1
                pos_2 = rune.straight_pos_2
                pos_3 = rune.straight_pos_3
            elif inverted == True:
                keys = rune.inverted_keys or f"для прямой руны: {rune.straight_keys}"
                meaning = (
                    rune.inverted_meaning or f"для прямой руны: {rune.straight_meaning}"
                )
                pos_1 = rune.inverted_pos_1 or f"для прямой руны: {rune.straight_pos_1}"
                pos_2 = rune.inverted_pos_2 or f"для прямой руны: {rune.straight_pos_2}"
                pos_3 = rune.inverted_pos_3 or f"для прямой руны: {rune.straight_pos_3}"

            if position == 1:
                position_text = pos_1
            elif position == 2:
                position_text = pos_2
            elif position == 3:
                position_text = pos_3

            # Отправляем описание руны
            await update.effective_message.reply_html(
                (
                    f"<b>{rune.symbol}</b> {rune.type}{' (Перевернуто)' if inverted else ''}"
                    f"\n\nКлючи: {keys}"
                    f"\n\nЗначение: {meaning}"
                    f"\n\nПоложение: {position_text}"
                ),
                reply_to_message_id=update.effective_message.message_id,
            )
        except Exception as e:
            logger.error(f"Ошибка при обработке callback-запроса: {e}", exc_info=True)
            await query.edit_message_text(
                "Произошла ошибка. Пожалуйста, попробуйте снова."
            )

    async def make_only_card_message(self, card, deck_id: int, card_index: int):
        """
        Формирует сообщение с картой (изображение, описание и кнопки).
        """
        # Формируем описание карты
        card_text = await self.format_card_name(card, False)

        # Формируем клавиатуру с кнопками "вперед" и "назад"
        keyboard = []
        if card_index > 0:
            keyboard.append(
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=f"allcard_{deck_id}_{card_index - 1}",
                )
            )
        if (
            card_index
            < (await TarotCardItem.objects.filter(deck_id=deck_id).acount()) - 1
        ):
            keyboard.append(
                InlineKeyboardButton(
                    text="➡️ Вперед",
                    callback_data=f"allcard_{deck_id}_{card_index + 1}",
                )
            )

        return card_text, InlineKeyboardMarkup([keyboard])

    async def handle_all_by_deck(self, update: Update, context: CallbackContext):
        """
        Обработчик команды /all deck <deck_id>.
        """
        try:
            msg_text = update.message.text
            logger.info(f"Обработка команды /all deck с текстом: {msg_text[:100]}")

            # Извлекаем deck_id из команды
            deck_id = int(msg_text.split()[-1])  # /all deck 8 -> 8
            # Получаем первую карту
            card = await self.get_cards(deck_id, 1, [0])
            card = card[0]
            if not card:
                await update.message.reply_text("Карты в этой колоде не найдены.")
                return

            # Формируем сообщение с первой картой
            card_text, keyboard = await self.make_only_card_message(card, deck_id, 0)

            # Отправляем изображение и описание карты
            await update.message.reply_photo(
                photo=(card["img_id"]),
                caption=card_text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Ошибка при обработке команды /all deck: {e}", exc_info=True)
            await update.message.reply_text(
                "Произошла ошибка при обработке вашего запроса. Пожалуйста, попробуйте снова."
            )

    async def handle_allcard_callback(self, update: Update, context: CallbackContext):
        """
        Обработчик callback-запросов для навигации между картами.
        """
        try:
            query = update.callback_query
            await query.answer()

            # Извлекаем данные из callback_data
            _, deck_id, card_index = query.data.split("_")
            deck_id = int(deck_id)
            card_index = int(card_index)

            # Получаем данные карты
            card = await self.get_cards(deck_id, 1, [card_index])
            card = card[0]
            if not card:
                await query.edit_message_text("Карта не найдена.")
                return

            # Формируем сообщение с картой
            card_text, keyboard = await self.make_only_card_message(
                card, deck_id, card_index
            )

            # Обновляем сообщение с новой картой
            await query.edit_message_media(
                InputMediaPhoto(
                    media=(card["img_id"]),
                    caption=card_text,
                    parse_mode="HTML",
                ),
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.error(f"Ошибка при обработке callback: {e}", exc_info=True)
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
