import re
import aiohttp
import json
import requests
from telegram import Update, InputMediaPhoto
from telegram.ext import CommandHandler, MessageHandler, CallbackContext, filters

from django.utils.timezone import now

from tg_bot.bot.abstract import AbstractBot
from tg_bot.models import TgUser, TarotDeck, TarotCardItem, TarotCard

from server.logger import logger
from django.conf import settings


class TarotBot(AbstractBot):
    def __init__(self):
        self.handlers = self.get_handlers()

    def get_handlers(self):
        return [
            MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & filters.Regex(r"^\/card(\d+)?"), self.handle_card)
        ]

    async def handle_card(self, update: Update, context: CallbackContext):
        """
        Обработчик команды /card.
        """
        msg_text = update.message.text

        # Парсинг параметров
        options = {}

        # Парсинг количества карт (counter)
        counter_found = re.search(r"(card)\d", msg_text)
        options["counter"] = int(counter_found.group(0).replace("card", "")) if counter_found else 1
        options["counter"] = max(1, min(options["counter"], 10))

        # Парсинг колоды (deck)
        deck_found = re.search(r"deck \d\d?", msg_text)
        options["deck"] = int(deck_found.group(0).replace("deck ", "")) if deck_found else None

        # Парсинг переворота карты (flip)
        options["flip"] = bool(re.search(r"flip", msg_text))

        # Парсинг ID карт (cardIds)
        card_ids_found = re.search(r"c\d\d?", msg_text)
        if card_ids_found:
            card_ids = list(map(int, card_ids_found.group(0).replace("c", "").split("_")))
            card_ids = [max(0, min(card_id, 77)) for card_id in card_ids]  # Ограничение от 0 до 77
            if len(card_ids) < options["counter"]:
                for j in range(len(card_ids), options["counter"]):
                    temp_id = j
                    while temp_id in card_ids:
                        temp_id += 1
                    card_ids.append(temp_id)
            options["card_ids"] = card_ids
        else:
            options["card_ids"] = None

        # Парсинг флага major
        options["major"] = bool(re.search(r"major", msg_text))

        # Логирование параметров (для отладки)
        logger.info(f"Options: {options}")

        # Отправка ответа пользователю
        await update.message.reply_text(options, reply_to_message_id=update.message.message_id)

