import re
import traceback
import time
from typing import List, Optional, Dict

import aiohttp
import random
from bs4 import BeautifulSoup

from telegram import Update, InputMediaPhoto, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    CallbackContext,
    filters,
)

from django.utils.timezone import now
from django.core.exceptions import ObjectDoesNotExist

from tg_bot.bot.abstract import AbstractBot
from tg_bot.models import (
    TgUser,
)
from tarot.models import (
    TarotDeck,
    TarotCardItem,
    TarotCard,
    ExtendedMeaning,
    TarotUserReading,
    OraculumDeck,
    OraculumItem,
    Rune,
)
from server.logger import logger
from django.conf import settings

reading_ids = {}
user_exclude_cards = {}


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
        ]

    async def save_reading(self, user, message_id, text):
        user, created = await TgUser.objects.aget_or_create(
            tg_id=user.id,
            defaults={
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "language_code": user.language_code,
                "is_bot": user.is_bot,
            },
        )

        reading = await TarotUserReading.objects.acreate(
            user=user,
            text=text,
            message_id=message_id,
        )
        reading_ids[user.id] = message_id
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
            raise ValueError("Указанный ID колоды не существует.")

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
                "tarot_card"
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
                raise ValueError(
                    f"Недостаточно карт. Доступно: {len(available_ids)}, требуется: {remaining}"
                )

            # 6. Случайная выборка через Python
            random_ids = random.sample(available_ids, remaining) if remaining else []

            # 7. Получаем случайные карты
            random_cards = [
                await base_query.aget(tarot_card__card_id=cid) for cid in random_ids
            ]

            # 8. Формируем результат
            combined = manual_cards + random_cards
            # logger.info(type(combined[0]))
            # logger.info(await combined[0])
            return [
                {
                    "card_instance": card,
                    "card_id": card.tarot_card.card_id,
                    "img_id": card.img_id,
                    "name": card.tarot_card.name,
                    "flipped": random.choice([True, False]),
                }
                for card in combined[:counter]
            ]

        except ObjectDoesNotExist as e:
            raise ValueError("Карта не найдена") from e
        except Exception as e:
            raise RuntimeError(f"Ошибка: {str(e)}") from e

    async def get_oraculum_cards(self, deck_id, counter, exclude_cards):
        try:
            # 1. Проверка существования колоды
            if not await OraculumDeck.objects.filter(id=deck_id).aexists():
                raise ValueError(f"Колода {deck_id} не найдена")

            # 2. Базовый запрос карт колоды
            base_query = OraculumItem.objects.filter(deck_id=deck_id)

            # 3. Исключение указанных карт
            if exclude_cards:
                base_query = base_query.exclude(id__in=exclude_cards)

            # 4. Получаем все доступные ID карт
            all_card_ids: List[int] = [
                card_id async for card_id in base_query.values_list("id", flat=True)
            ]

            if len(all_card_ids) < counter:
                raise ValueError(
                    f"Недостаточно карт в колоде. Требуется: {counter}, доступно: {len(all_card_ids)}"
                )

            # 5. Выборка случайных ID карт
            random_ids: List[int] = random.sample(all_card_ids, counter)

            # 6. Получение карт по выбранным ID
            random_cards: List[OraculumItem] = [
                await base_query.aget(id=cid) for cid in random_ids
            ]

            # 7. Формирование результата
            return [
                {
                    "card_instance": card,
                    "card_id": card.id,
                    "img_id": card.img_id,
                    "name": card.name,
                    "flipped": random.choice([True, False]),  # Переворот карты
                }
                for card in random_cards
            ]

        except ObjectDoesNotExist as e:
            raise ValueError("Карта не найдена") from e
        except Exception as e:
            raise RuntimeError(f"Ошибка: {str(e)}") from e

    async def format_card_name(self, card, flip):
        return "\n".join(
            [
                str(item)
                for item in [
                    card["name"],
                    "Перевернуто" if flip and card["flipped"] else None,
                    (
                        f'{card["card_instance"].description} '
                        + (
                            card["card_instance"].inverted
                            if flip and card["flipped"]
                            else card["card_instance"].direct
                        )
                        if isinstance(card["card_instance"], OraculumItem)
                        else None
                    ),
                ]
                if item is not None
            ]
        )

    async def send_card(self, update: Update, cards, meaning_cards, deck, major, flip):
        logger.info(
            f"значение: {meaning_cards} колода: {deck} старшие: {major} {int(major)} перевернуто: {flip} {int(flip)}"
        )
        await update.effective_message.reply_media_group(
            [
                InputMediaPhoto(
                    (
                        "https://placecats.com/300/200"
                        if settings.TG_DEBUG
                        else c["img_id"]
                    ),
                    await self.format_card_name(c, flip),
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
        params = {
            "text": "\n".join(text),
            "reply_to_message_id": update.effective_message.message_id,
            "disable_web_page_preview": True,
        }
        if reply_markup:
            params["reply_markup"] = InlineKeyboardMarkup(reply_markup)

        await update.effective_message.reply_text(**params)

    async def handle_card(self, update: Update, context: CallbackContext):
        """
        Обработчик команды /card.
        """
        msg_text = update.message.text
        logger.info(f"Обработка команды /card с текстом: {msg_text[:100]}")

        # Парсинг параметров
        options: Dict[str, any] = {}

        try:
            # Парсинг количества карт (counter)
            counter_found = re.search(r"(card)\d", msg_text)
            options["counter"] = (
                int(counter_found.group(0).replace("card", "")) if counter_found else 1
            )
            options["counter"] = max(1, min(options.get("counter", 1), 10))
            logger.info(f"Парсинг количества карт: counter={options.get('counter')}")

            # Парсинг колоды (deck)
            deck_found = re.search(r"deck \d+", msg_text)
            options["deck"] = (
                int(deck_found.group(0).replace("deck ", "")) if deck_found else None
            )
            logger.info(f"Парсинг колоды: deck={options.get('deck')}")

            # Парсинг переворота карты (flip)
            options["flip"] = bool(re.search(r"flip", msg_text))
            logger.info(f"Парсинг переворота карты: flip={options.get('flip')}")

            # Парсинг ID карт (cardIds)
            card_ids_found = re.findall(r"c(\d+(?:_\d+)*)", msg_text)
            if card_ids_found:
                # Преобразуем найденные ID в числа и ограничиваем их от 0 до 77
                card_ids = [int(c) % 78 for c in card_ids_found[0].split("_")]
                if len(card_ids) < options.get("counter", 1):
                    temp_id = card_ids[-1]
                    for j in range(len(card_ids), options.get("counter", 1)):
                        temp_id = (temp_id + 1) % 78
                        card_ids.append(temp_id)
                options["card_ids"] = card_ids[: options.get("counter", 1)]
                logger.info(f"Парсинг ID карт: card_ids={options.get('card_ids')}")
            else:
                options["card_ids"] = None
                logger.info("ID карт не указаны, будут выбраны случайные карты.")

            # Парсинг флага major
            options["major"] = bool(re.search(r"major", msg_text))
            logger.info(f"Парсинг флага major: major={options.get('major')}")

            # Получение колоды
            deck = await self.get_deck(options.get("deck"))
            logger.info(f"Используемая колода: {deck.id if deck else 'не указана'}")

            # Получение карт
            cards = await self.get_cards(
                deck.id if deck else None,
                options.get("counter", 1),
                options.get("card_ids"),
                options.get("major", False),
            )
            logger.info(f"Получено карт: {len(cards)}")
            reading = await self.save_reading(
                update.effective_user,
                update.effective_message.message_id,
                f"ТАРО: {deck.name} "
                + ", ".join(
                    [await self.format_card_name(c, options["flip"]) for c in cards]
                ),
            )

            logger.info(f"Результат гадания сохранен: {reading}")
            user_exclude_cards[update.effective_user.id] = [c["card_id"] for c in cards]

            # Отправка карт
            await self.send_card(
                update,
                cards,
                [c["card_id"] for c in cards],
                deck,
                options.get("major", False),
                options.get("flip", False),
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

        # Парсинг параметров
        options: Dict[str, any] = {}

        try:
            # Парсинг количества карт (counter)
            counter_found = re.search(r"(oraculum)\d", msg_text)
            options["counter"] = (
                int(counter_found.group(0).replace("oraculum", ""))
                if counter_found
                else 1
            )
            options["counter"] = max(1, min(options.get("counter", 1), 10))
            logger.info(f"Парсинг количества карт: counter={options.get('counter')}")

            # Парсинг колоды (deck)
            deck_found = re.search(r"deck \d+", msg_text)
            options["deck"] = (
                int(deck_found.group(0).replace("deck ", "")) if deck_found else None
            )
            logger.info(f"Парсинг колоды: deck={options.get('deck')}")

            # Парсинг переворота карты (flip)
            options["flip"] = bool(re.search(r"flip", msg_text))
            logger.info(f"Парсинг переворота карты: flip={options.get('flip')}")

            deck = await self.get_deck(options.get("deck"), "oraculum")
            logger.info(f"Используемая колода: {deck.id if deck else 'не указана'}")

            # Получение карт
            cards = await self.get_oraculum_cards(
                deck.id if deck else None, options.get("counter", 1), []
            )
            logger.info(f"Получено карт: {len(cards)}")
            reading = await self.save_reading(
                update.effective_user,
                update.effective_message.message_id,
                f"ОРАКУЛ: {deck.name} "
                + ", ".join(
                    [await self.format_card_name(c, options["flip"]) for c in cards]
                ),
            )

            logger.info(f"Результат гадания сохранен: {reading}")
            user_exclude_cards[update.effective_user.id] = [c["card_id"] for c in cards]

            # Отправка карт
            await self.send_card(
                update,
                cards,
                [],
                deck,
                options.get("major", False),
                options.get("flip", False),
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

        try:
            _, deck_id, major, flip = query.data.split("_")
            major = int(major)
            flip = int(flip)
            exclude_cards = user_exclude_cards.get(update.effective_user.id, [])
        except ValueError as e:
            logger.error(f"Ошибка при разборе query.data: {query.data}, ошибка: {e}")
            await query.edit_message_text("Ошибка обработки запроса.")
            return

        logger.info(
            f"Запрошена ещё одна карта для колоды {deck_id}. Исключаемые карты: {exclude_cards}"
        )

        try:
            deck = await self.get_deck(int(deck_id), "oraculum")
            logger.info(f"Загружена колода: {deck}")
        except Exception as e:
            logger.error(f"Ошибка загрузки колоды {deck_id}: {e}")
            await query.edit_message_text("Ошибка загрузки колоды.")
            return

        try:
            new_card = await self.get_oraculum_cards(
                deck_id=int(deck_id),
                counter=1,
                exclude_cards=exclude_cards,
            )
            # logger.info(f"Выбраны новые карты: {new_card}")
            new_card_text = "\n".join(
                [",".join([await self.format_card_name(c, flip) for c in new_card])]
            )

        except Exception as e:
            logger.error(f"Ошибка получения карт: {e}", exc_info=True)
            logger.error(traceback.format_exc())
            await query.edit_message_text("Ошибка получения карт.")
            return

        if not new_card:
            logger.info("Нет доступных карт для выбора.")
            await update.effective_message.reply_text("Нет доступных карт для выбора.")
            return

        full_exclude = exclude_cards + [c["card_id"] for c in new_card]
        logger.info(f"Обновленный список исключений: {full_exclude}")

        try:
            user, _ = await TgUser.objects.aget_or_create(
                tg_id=update.effective_user.id,
                defaults={
                    "username": update.effective_user.username,
                    "first_name": update.effective_user.first_name,
                    "last_name": update.effective_user.last_name,
                    "language_code": update.effective_user.language_code,
                    "is_bot": update.effective_user.is_bot,
                },
            )
            initial_message_id = reading_ids.get(user.id)
            reading, created = await TarotUserReading.objects.aupdate_or_create(
                message_id=initial_message_id, user=user
            )
            reading.text = (
                f"ОРАКУЛ: {deck.name} {new_card_text}"
                if created
                else f"{reading.text}, {new_card_text}"
            )
            reading.data = now()
            await reading.asave()

            user_exclude_cards[update.effective_user.id] = full_exclude

            await self.send_card(
                update,
                new_card,
                [c["card_id"] for c in new_card],
                deck,
                bool(major),
                bool(flip),
            )
            logger.info(
                f"Карта отправлена: {[str(n.get(k)) for k in ['name'] for n in new_card]}"
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке карты: {e}")
            await query.edit_message_text("Ошибка при отправке карты.")

    async def handle_more_button(self, update: Update, context: CallbackContext):
        query = update.callback_query
        await query.answer()
        logger.info(f"Получен callback-запрос: {query.data}")

        try:
            _, deck_id, major, flip = query.data.split("_")
            major = int(major)
            flip = int(flip)
            exclude_cards = user_exclude_cards.get(update.effective_user.id, [])
        except ValueError as e:
            logger.error(f"Ошибка при разборе query.data: {query.data}, ошибка: {e}")
            await query.edit_message_text("Ошибка обработки запроса.")
            return

        logger.info(
            f"Запрошена ещё одна карта для колоды {deck_id}. Исключаемые карты: {exclude_cards}"
        )

        try:
            deck = await self.get_deck(int(deck_id))
            logger.info(f"Загружена колода: {deck}")
        except Exception as e:
            logger.error(f"Ошибка загрузки колоды {deck_id}: {e}")
            await query.edit_message_text("Ошибка загрузки колоды.")
            return

        try:
            new_card = await self.get_cards(
                deck_id=int(deck_id),
                counter=1,
                card_ids=None,
                exclude_cards=exclude_cards,
                major=bool(major),
            )
            # logger.info(f"Выбраны новые карты: {new_card}")
            new_card_text = "\n".join(
                [",".join([await self.format_card_name(c, flip) for c in new_card])]
            )

        except Exception as e:
            logger.error(f"Ошибка получения карт: {e}", exc_info=True)
            logger.error(traceback.format_exc())
            await query.edit_message_text("Ошибка получения карт.")
            return

        if not new_card:
            logger.info("Нет доступных карт для выбора.")
            await update.effective_message.reply_text("Нет доступных карт для выбора.")
            return

        full_exclude = exclude_cards + [c["card_id"] for c in new_card]
        logger.info(f"Обновленный список исключений: {full_exclude}")

        try:
            user, _ = await TgUser.objects.aget_or_create(
                tg_id=update.effective_user.id,
                defaults={
                    "username": update.effective_user.username,
                    "first_name": update.effective_user.first_name,
                    "last_name": update.effective_user.last_name,
                    "language_code": update.effective_user.language_code,
                    "is_bot": update.effective_user.is_bot,
                },
            )
            initial_message_id = reading_ids.get(user.id)
            reading, created = await TarotUserReading.objects.aupdate_or_create(
                message_id=initial_message_id, user=user
            )
            reading.text = (
                f"ТАРО: {deck.name} {new_card_text}"
                if created
                else f"{reading.text}, {new_card_text}"
            )
            reading.data = now()
            await reading.asave()

            user_exclude_cards[update.effective_user.id] = full_exclude

            await self.send_card(
                update,
                new_card,
                [c["card_id"] for c in new_card],
                deck,
                bool(major),
                bool(flip),
            )
            logger.info(
                f"Карта отправлена: {[str(n.get(k)) for k in ['name'] for n in new_card]}"
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке карты: {e}")
            await query.edit_message_text("Ошибка при отправке карты.")

    def split_text(self, text, chunk_size=1024):
        lines = text.split(
            "\n"
        )  # Разделяем по переводам строк — сохраняем каждую строку как есть
        chunks = []
        current_chunk = ""

        for line in lines:
            # Длина строки + длина текущего куска + 1 символ на '\n' (если кусок не пустой)
            needed_length = len(line)
            if current_chunk:  # Если уже что-то есть — добавим '\n' перед новой строкой
                needed_length += 1

            # Проверяем, поместится ли строка в текущий кусок
            if len(current_chunk) + needed_length > chunk_size:
                # Если нет — сохраняем текущий кусок и начинаем новый с этой строки
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = line
            else:
                # Если да — добавляем строку с переводом строки
                if current_chunk:
                    current_chunk += "\n" + line
                else:
                    current_chunk = line

        # Не забываем добавить последний кусок
        if current_chunk:
            chunks.append(current_chunk)

        return chunks

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

        reading = await self.save_reading(
            update.effective_user,
            update.effective_message.message_id,
            f"ONE: " + f"{random_card['name']}\n{random_card['url']}",
        )

        logger.info(f"Результат гадания сохранен: {reading}")

        await update.effective_message.reply_photo(
            random_card["img"],
            f"{random_card['name']}\n{random_card['url']}",
            reply_to_message_id=update.effective_message.message_id,
        )
        await context.bot.delete_message(update.effective_chat.id, tech_msg_id)

    async def handle_last_readings(self, update: Update, context: CallbackContext):
        # Получаем пользователя по tg_id
        try:
            user = await TgUser.objects.aget(tg_id=update.effective_user.id)
            user_readings = []
            async for item in (
                TarotUserReading.objects.filter(user=user)
                .select_related("user")
                .order_by("-date")[:5]
            ):
                user_readings.append(f"{item.date}: {item.text}")

            if not user_readings:
                await update.message.reply_text("Гаданий не найдено.")
                return
            await update.effective_message.reply_text(
                "\n".join(user_readings),
                reply_to_message_id=update.effective_message.message_id,
            )
        except Exception as e:
            logger.error(e)

    async def make_decks_page(
        self,
        current_page: int = 0,
        items_per_page: int = 13,
        deck_type: str = "tarot",
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
        logger.info(f"Обработка команды /futark с текстом: {msg_text[:100]}")

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
                reading_data = await self.save_reading(
                    update.effective_user,
                    update.effective_message.message_id,
                    f"FUTARK: {' '.join(rune_texts)}",
                )

                logger.info(f"Результат гадания сохранен: {reading_data}")

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
                logger.info(random_rune.symbol)
                # Формируем текст сообщения
                text_parts = [f"<b>{random_rune.symbol}</b>", random_rune.type]
                if inverted:
                    text_parts.append("Перевернуто")

                reading_data = await self.save_reading(
                    update.effective_user,
                    update.effective_message.message_id,
                    f"FUTARK: {' '.join(text_parts)}",
                )

                logger.info(f"Результат гадания сохранен: {reading_data}")

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
                photo=(
                    "https://placecats.com/300/200"
                    if settings.TG_DEBUG
                    else card["img_id"]
                ),
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
                    media=(
                        "https://placecats.com/300/200"
                        if settings.TG_DEBUG
                        else card["img_id"]
                    ),
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

❓ <b>Помощь:</b>
<code>/help</code> - Показать это сообщение.

📌 <b>Примеры:</b>
<code>/card deck 1</code> - Сделать расклад из колоды Таро с ID 1.
<code>/oraculum3 flip</code> - Сделать расклад из 3 карт Оракула с возможностью перевернутых карт.
<code>/futark triplet</code> - Сделать расклад из 3 рун.
"""

        await update.message.reply_text(help_text, parse_mode="HTML")
