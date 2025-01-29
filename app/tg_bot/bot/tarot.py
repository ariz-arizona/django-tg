import re
import random
import time
from typing import List, Optional, Dict
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
from tg_bot.models import TgUser, TarotDeck, TarotCardItem, TarotCard, ExtendedMeaning

from server.logger import logger
from django.conf import settings


class TarotBot(AbstractBot):
    def __init__(self):
        self.handlers = self.get_handlers()

    def get_handlers(self):
        return [
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
                self.handle_pagination, pattern=r"^meaning_[a-z]+_\d+_\d+$"
            ),
        ]

    async def get_deck(self, deck_id=None):
        # Получаем список всех ID колод
        deck_ids: List[int] = [deck.id async for deck in TarotDeck.objects.all()]
        logger.info(f"Получаем колоду с ID {deck_id}")

        if not deck_ids:
            raise ValueError("Нет доступных колод.")

        if deck_id is not None and deck_id not in deck_ids:
            raise ValueError("Указанный ID колоды не существует.")

        if deck_id is None:
            deck_id = random.choice(deck_ids)

        # Получаем колоду по ID
        try:
            return await TarotDeck.objects.aget(id=deck_id)
        except TarotDeck.DoesNotExist:
            # На случай, если колода была удалена между получением списка ID и запросом
            raise ValueError("Не удалось получить колоду.")

    async def get_cards(
        self,
        deck_id: int,
        counter: int = 1,
        card_ids: Optional[List[str]] = None,  # Используем card_id (str)
        major: bool = False,
        exclude_cards: Optional[List[str]] = None,  # Новый параметр для исключения карт
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
            return [
                {
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

    async def send_card(self, update: Update, cards, exclude_cards, deck, major):
        await update.effective_message.reply_media_group(
            [
                InputMediaPhoto(
                    (
                        "https://placecats.com/300/200"
                        if settings.TG_DEBUG
                        else c["img_id"]
                    ),
                    c["name"],
                )
                for c in cards
            ],
            reply_to_message_id=update.effective_message.message_id,
        )
        await update.effective_message.reply_text(
            "\n".join([deck.name, deck.link]),
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Еще карту",
                            callback_data=f'more_{deck.id}_{"#".join(exclude_cards)}_{int(major)}',
                        ),
                        InlineKeyboardButton(
                            "Базовые значения",
                            callback_data=f'desc_{"#".join(exclude_cards)}',
                        ),
                    ]
                ]
            ),
            reply_to_message_id=update.effective_message.message_id,
            disable_web_page_preview=True,
        )

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

            # Отправка карт
            await self.send_card(
                update,
                cards,
                [c["card_id"] for c in cards],
                deck,
                options.get("major", False),
            )
            logger.info("Карты успешно отправлены.")

        except Exception as e:
            logger.error(f"Ошибка при обработке команды /card: {e}", exc_info=True)
            await update.message.reply_text(
                "Произошла ошибка при обработке вашего запроса. Пожалуйста, попробуйте снова."
            )

    async def handle_more_button(self, update: Update, context: CallbackContext):
        query = update.callback_query
        await query.answer()
        logger.info(f"Получен callback-запрос: {query.data}")

        try:
            _, deck_id, exclude_cards, major = query.data.split("_")
            exclude_cards = exclude_cards.split("#") if exclude_cards else []
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
            logger.info(f"Выбраны новые карты: {new_card}")
        except Exception as e:
            logger.error(f"Ошибка получения карт: {e}")
            await query.edit_message_text("Ошибка получения карт.")
            return

        if not new_card:
            logger.info("Нет доступных карт для выбора.")
            await query.edit_message_text("Нет доступных карт для выбора.")
            return

        full_exclude = exclude_cards + [c["card_id"] for c in new_card]
        logger.info(f"Обновленный список исключений: {full_exclude}")

        try:
            await self.send_card(update, new_card, full_exclude, deck, bool(major))
            logger.info(f"Карта отправлена: {new_card}")
        except Exception as e:
            logger.error(f"Ошибка при отправке карты: {e}")
            await query.edit_message_text("Ошибка при отправке карты.")

    def split_text(self, text, chunk_size=1024):
        words = text.split()  # Разбиваем текст на слова
        chunks = []
        current_chunk = ""

        for word in words:
            # Проверяем, не превысит ли добавление слова лимит chunk_size
            if len(current_chunk) + len(word) + 1 > chunk_size:  # +1 для пробела
                chunks.append(current_chunk)
                current_chunk = word  # Начинаем новый кусок с текущего слова
            else:
                current_chunk = f"{current_chunk} {word}".strip()  # Добавляем слово

        if current_chunk:  # Добавляем последний кусок, если он есть
            chunks.append(current_chunk)

        return chunks

    async def create_pagination_keyboard(
        self, meaning_type, card_id, current_page, total_pages
    ):
        try:
            logger.info(
                f"Создание клавиатуры для card_id={card_id}, тип {meaning_type}, страница {current_page} из {total_pages}"
            )

            # Загружаем значения с prefetch_related
            all_meanings = ExtendedMeaning.objects.prefetch_related(
                "tarot_card"
            ).filter(tarot_card__card_id=card_id)

            # Собираем все значения в список
            meanings_list = [item.category async for item in all_meanings]
            meanings_list.append('base')
            meanings_list.remove(meaning_type)
            if not meanings_list:
                logger.warning(f"Для карты {card_id} не найдено значений.")
                return None  # или вернуть InlineKeyboardMarkup([]), если нужна пустая клавиатура

            logger.info(f"Найдено {len(meanings_list)} значений для карты {card_id}")

            keyboard = []

            # Кнопки навигации
            paged_row = []
            if current_page > 1:
                paged_row.append(
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data=f"meaning_{meaning_type}_{card_id}_{current_page - 1}",
                    )
                )
            if current_page < total_pages:
                paged_row.append(
                    InlineKeyboardButton(
                        "Вперед ➡️",
                        callback_data=f"meaning_{meaning_type}_{card_id}_{current_page + 1}",
                    )
                )
            if paged_row:
                keyboard.append(paged_row)

            # Группируем кнопки по 2 в массив массивов
            grouped_buttons = [
                meanings_list[i : i + 2] for i in range(0, len(meanings_list), 2)
            ]

            # Итерация по сгруппированным данным
            for group in grouped_buttons:
                row = [
                    InlineKeyboardButton(
                        text=item,
                        callback_data=f"meaning_{item}_{card_id}_1",
                    )
                    for item in group
                ]
                keyboard.append(row)

            return InlineKeyboardMarkup(keyboard)

        except Exception as e:
            logger.error(f"Ошибка в create_pagination_keyboard: {e}", exc_info=True)
            return None  # Возвращаем None, если произошла критическая ошибка

    # Функция для отправки текста с пагинацией
    async def send_paginated_text(self, update: Update, card_id, text):
        # Разделяем текст на части
        text_parts = self.split_text(text)
        total_pages = len(text_parts)
        keyboard = await self.create_pagination_keyboard(
            "base", card_id, 1, total_pages
        )
        # Отправляем первую страницу
        await update.effective_message.reply_text(
            text=text_parts[0], reply_markup=keyboard
        )

    # Обработчик для навигации по страницам
    async def handle_pagination(self, update: Update, context: CallbackContext):
        query = update.callback_query
        await query.answer()

        # Извлекаем данные из callback_data
        _, meaning_type, card_id, page = query.data.split("_")
        page = int(page)

        if meaning_type == "base":
            card = await TarotCard.objects.aget(card_id=card_id)
            text = card.meaning
        else:
            cards = ExtendedMeaning.objects.all().prefetch_related("tarot_card")
            card = await cards.filter(
                tarot_card__card_id=card_id, category=meaning_type
            ).aget()
            text = card.text

        text_parts = self.split_text(text)
        keyboard = await self.create_pagination_keyboard(
            meaning_type, card_id, page, len(text_parts)
        )

        # Обновляем сообщение с новой страницей
        await query.edit_message_text(
            text=text_parts[page - 1],
            reply_markup=keyboard,
        )

    async def handle_desc_button(self, update: Update, context: CallbackContext):
        query = update.callback_query
        await query.answer()

        _, cards = query.data.split("_")
        cards = cards.split("#")

        for c in cards:
            card = await TarotCard.objects.aget(card_id=c)
            await self.send_paginated_text(update, card.card_id, card.meaning)
            time.sleep(0.3)
