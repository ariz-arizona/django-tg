import re
import random
from typing import List, Optional
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
from tg_bot.models import TgUser, TarotDeck, TarotCardItem, TarotCard

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
            CallbackQueryHandler(self.handle_desc_button, pattern=r"^desc$"),
        ]

    async def get_deck(self, index=None):
        # Получаем общее количество колод
        total_decks = await TarotDeck.objects.acount()

        if total_decks == 0:
            raise ValueError("Нет доступных колод.")

        if index is not None and index >= total_decks:
            index = None

        if index is None:
            index = random.randint(0, total_decks - 1)

        # Получаем колоду по индексу
        try:
            qs = TarotDeck.objects.all()[index : index + 1]
            return await qs.aget()

        except IndexError:
            # На случай, если колоды были удалены между count() и запросом
            raise ValueError("Не удалось получить случайную колоду.")

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
            # [InputMediaPhoto(c["img_id"], c["name"]) for c in cards],
            [
                InputMediaPhoto("https://placecats.com/300/200", c["name"])
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
                        InlineKeyboardButton("Базовые значения", callback_data="desc"),
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

        # Парсинг параметров
        options = {}

        # Парсинг количества карт (counter)
        counter_found = re.search(r"(card)\d", msg_text)
        options["counter"] = (
            int(counter_found.group(0).replace("card", "")) if counter_found else 1
        )
        options["counter"] = max(1, min(options["counter"], 10))

        # Парсинг колоды (deck)
        deck_found = re.search(r"deck \d+", msg_text)
        options["deck"] = (
            int(deck_found.group(0).replace("deck ", "")) if deck_found else None
        )

        # Парсинг переворота карты (flip)
        options["flip"] = bool(re.search(r"flip", msg_text))

        # Парсинг ID карт (cardIds)
        card_ids_found = re.findall(r"c(\d+(?:_\d+)*)", msg_text)
        if card_ids_found:
            # Преобразуем найденные ID в числа и ограничиваем их от 0 до 77
            card_ids = [int(c) % 78 for c in card_ids_found[0].split("_")]
            if len(card_ids) < options["counter"]:
                temp_id = card_ids[-1]
                for j in range(len(card_ids), options["counter"]):
                    temp_id = (temp_id + 1) % 78
                    card_ids.append(temp_id)
            options["card_ids"] = card_ids[0 : options["counter"]]
        else:
            options["card_ids"] = None

        # Парсинг флага major
        options["major"] = bool(re.search(r"major", msg_text))

        deck = await self.get_deck(options["deck"])
        cards = await self.get_cards(
            deck.id, options["counter"], options["card_ids"], options["major"]
        )
        await self.send_card(
            update, cards, [c["card_id"] for c in cards], deck, options["major"]
        )

    async def handle_more_button(self, update: Update, context: CallbackContext):
        query = update.callback_query
        await query.answer()

        _, deck_id, exclude_cards, major = query.data.split("_")
        exclude_cards = exclude_cards.split("#") if exclude_cards else []

        logger.info(
            f"Запрошена ещё одна карта для колоды {deck_id}. "
            f"Исключаемые карты: {exclude_cards}"
        )

        deck = await self.get_deck(int(deck_id))
        new_card = await self.get_cards(
            deck_id=int(deck_id),
            counter=1,
            card_ids=None,
            exclude_cards=exclude_cards,
            major=bool(major),
        )

        if not new_card:
            await query.edit_message_text("Нет доступных карт для выбора.")
            return
        full_exclude = exclude_cards + [c["card_id"] for c in new_card]
        await self.send_card(
            update,
            new_card,
            full_exclude,
            deck,
            bool(major),
        )

    async def handle_desc_button(self, update: Update, context: CallbackContext):
        query = update.callback_query
        await query.answer()
        logger.info(query)
