# allcard_handler.py
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    MessageHandler,
    CallbackQueryHandler,
    CallbackContext,
    filters
)
from django.db.models import IntegerField
from django.db.models.functions import Cast

from tarot.models import (
    TarotCardItem,
    OraculumItem,
)
from server.logger import logger


class AllCardHandler:
    """Обработчик команды /all и связанных callback'ов."""

    def __init__(self, bot_instance):
        """
        Инициализация обработчика.

        Args:
            bot_instance: Экземпляр основного бота для доступа к его методам и атрибутам
        """
        self.bot = bot_instance
    
    @property
    def app_bot_id(self):
        """Получаем app_bot_id из основного бота."""
        return self.bot.app_bot_id

    def get_handlers(self):
        """Возвращает список обработчиков для этой команды."""
        return [
            MessageHandler(
                filters.COMMAND
                & filters.TEXT
                & filters.ChatType.PRIVATE
                & filters.Regex(r"^\/all?"),
                self.handle_all_by_deck,
            ),
            CallbackQueryHandler(
                self.handle_allcard_callback,
                pattern=r"^allcard_",
            ),
        ]

    async def make_only_card_message(
        self,
        card,
        target_id: int | str,
        card_index: int,
        item_type: str = "deck"
    ):
        """Универсальный сборщик сообщений."""
        try:
            card_text = f"{card.tarot_card} из {card.deck}"
        except Exception:
            card_text = f"{card.tarot_card_id} из {card.deck_id}"

        img_id = await card.aget_file_id(self.app_bot_id)

        # Определяем лимит для кнопок в зависимости от типа
        if item_type == "card":
            total_count = await TarotCardItem.objects.filter(tarot_card__card_id=target_id).acount()
        elif item_type == "deck":
            total_count = await TarotCardItem.objects.filter(deck_id=target_id).acount()
        elif item_type == "all":
            total_count = await TarotCardItem.objects.acount()
        elif item_type == "oraculum-deck":
            total_count = await OraculumItem.objects.filter(deck_id=target_id).acount()
        elif item_type == "oraculum":
            total_count = await OraculumItem.objects.acount()
        else:
            total_count = 0

        keyboard = []

        if card_index > 0:
            keyboard.append(
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=f"allcard_{item_type}_{target_id}_{card_index - 1}",
                )
            )

        if card_index < total_count - 1:
            keyboard.append(
                InlineKeyboardButton(
                    text="➡️ Вперед",
                    callback_data=f"allcard_{item_type}_{target_id}_{card_index + 1}",
                )
            )

        return img_id, card_text, InlineKeyboardMarkup([keyboard])

    async def fetch_card_by_type(self, item_type: str, target_id: int | None, card_index: int):
        """Универсальный метод получения карты по типу."""
        logger.info(f"собираем тип {item_type} с целью {target_id} для индекса {card_index}")

        if item_type == "card":
            base_query = TarotCardItem.objects.filter(tarot_card__card_id=target_id)
        elif item_type == "deck":
            base_query = TarotCardItem.objects.filter(
                deck_id=target_id
            ).order_by(Cast('tarot_card__card_id', output_field=IntegerField()))
        elif item_type == "all":
            base_query = TarotCardItem.objects.all().order_by(
                'deck_id',
                Cast('tarot_card__card_id', output_field=IntegerField())
            )
        elif item_type == "oraculum-deck":
            base_query = OraculumItem.objects.filter(deck_id=target_id)
        elif item_type == "oraculum":
            base_query = OraculumItem.objects.all()
        else:
            return None

        qs = base_query.select_related('tarot_card', 'deck').prefetch_related("files")[card_index : card_index + 1]
        async for card in qs:
            return card
        return None

    async def handle_all_by_deck(self, update: Update, context: CallbackContext):
        """Обработчик команды /all."""
        try:
            msg_text = update.message.text
            # Используем метод парсинга из основного бота
            options = self.bot.parse_reading_options(msg_text)

            deck_id = options.get('deck')
            item_type = "all"
            target_id = None

            if options.get('card_ids'):
                item_type = 'card'
                target_id = options.get('card_ids')[0]
            elif options.get('deck'):
                item_type = 'deck'
                target_id = deck_id

            card = await self.fetch_card_by_type(item_type, target_id, 0)

            if not card:
                await update.message.reply_text("Карты не найдены.")
                return

            img_id, card_text, keyboard = await self.make_only_card_message(
                card, target_id if target_id else "None", 0, item_type=item_type
            )

            await update.message.reply_photo(
                photo=img_id,
                caption=card_text,
                reply_markup=keyboard,
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Ошибка: {e}", exc_info=True)
            await update.message.reply_text("Произошла ошибка при обработке запроса.")

    async def handle_allcard_callback(self, update: Update, context: CallbackContext):
        """Обработчик callback'ов для навигации по картам."""
        try:
            query = update.callback_query
            await query.answer()

            _, item_type, target_id, card_index = query.data.split("_")
            target_id = int(target_id) if target_id != "None" else None
            card_index = int(card_index)

            card = await self.fetch_card_by_type(item_type, target_id, card_index)

            if not card:
                await query.edit_message_text("Карта не найдена.")
                return

            img_id, card_text, keyboard = await self.make_only_card_message(
                card, target_id if target_id is not None else "None", card_index, item_type=item_type
            )

            await query.edit_message_media(
                InputMediaPhoto(
                    media=img_id,
                    caption=card_text,
                    parse_mode="HTML",
                ),
                reply_markup=keyboard,
            )
        except Exception as e:
            logger.error(f"Ошибка в колбэке: {e}", exc_info=True)
            await query.edit_message_text("Произошла ошибка.")