import re
import os
import io
import asyncio
from typing import List, Optional, Dict
from collections import Counter
import random
from datetime import datetime

import redis.asyncio as aioredis
from PIL import Image, ImageDraw, ImageFont

from telegram import (
    Update, InputMediaPhoto, InlineKeyboardButton,
    InlineKeyboardMarkup, MessageEntity
)
from telegram.ext import (
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    CallbackContext,
    filters,
)
from telegram.constants import ParseMode

from tg_bot.models import BotFileCache

from tarot.messages import CanvasMessages, CANVAS_3_TRIGGER
from tarot.models import (
    TarotDeck,
    TarotCardItem,
    TarotCardSticker,
    UserReading,
)

from server.logger import logger

# Импортируем унифицированные утилиты
from tarot.utils.image_utils import load_rws_image_from_disk, get_rws_cache_path
from tarot.utils.image_utils import create_spread_image as utils_create_spread_image
from tarot.utils.image_utils import (
    get_rws_card_image,
    create_full_spread_image,
    RWS_CACHE_DIR,
    CARD_WIDTH,
    CARD_HEIGHT,
    CARD_MARGIN,
    CANVAS_WIDTH,
    CANVAS_HEIGHT,
    CANVAS_BG,
)


# Инициализируем асинхронный клиент
redis_client = aioredis.StrictRedis(
    host=os.getenv("REDIS_HOST", "localhost"), 
    port=int(os.getenv("REDIS_PORT", 6379)), 
    db=3,
    decode_responses=True
)
redis_client_bot = aioredis.StrictRedis(
    host=os.getenv("REDIS_HOST", "localhost"), 
    port=int(os.getenv("REDIS_PORT", 6379)), 
    db=2,
    decode_responses=True
)

REDIS_TTL_SECONDS = 10
REDIS_KEY_TEMPLATE = "user:{user_id}:{category}"

# Константы для RWS-рендерера
RWS_DECK_ID = 56


class CanvasHandler:
    """Обработчик callback'ов для отрисовки расклада в Rider-Waite-Smith."""

    RWS_RENDER_CALLBACK = "rws_render"

    def __init__(self, bot_instance):
        self.bot = bot_instance
        self._ensure_cache_dir()
        self._font = None
        self._card_images: Dict[str, Image.Image] = {}
        self.messages = CanvasMessages()

    def _ensure_cache_dir(self):
        """Создаёт директорию для кэша RWS-карт, если её нет."""
        os.makedirs(RWS_CACHE_DIR, exist_ok=True)

    def get_handlers(self):
        """Возвращает список обработчиков."""
        return [
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
            CallbackQueryHandler(
                self.handle_rws_render,
                pattern=f"^rwsrender_\d*$"
            ),
        ]

    async def _load_font(self):
        """Ленивая загрузка шрифта."""
        if self._font is None:
            try:
                self._font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
            except:
                self._font = ImageFont.load_default()
        return self._font

    # ============ ПОЛУЧЕНИЕ ССЫЛКИ НА ФАЙЛ (для spread) ============
    async def _get_card_file_link(self, card_item: TarotCardItem) -> Optional[str]:
        """
        Получает публичную ссылку на файл карты.
        Используется в spread — только получает ссылку, НЕ скачивает и НЕ сохраняет.
        """
        bot_file = await card_item.files.afirst()
        if not bot_file:
            logger.warning(f"Нет исходного файла для карты {card_item.id}")
            return None

        file_link = await BotFileCache.acreate_and_get_link(bot_file=bot_file)
        if not file_link:
            logger.warning(f"Не удалось создать кэш-ссылку для карты {card_item.id}")
            return None

        return file_link

    # ============ ПОЛУЧЕНИЕ RWS-ИЗОБРАЖЕНИЯ (для render) — теперь через utils ============
    async def _get_rws_card_image(self, card_item: TarotCardItem) -> Optional[Image.Image]:
        """
        Получает изображение карты RWS через унифицированную утилиту.
        Кэширует результат в память хендлера.
        """
        card_key = f"{card_item.id}_{card_item.display_name}"

        # Проверяем память хендлера
        if card_key in self._card_images:
            return self._card_images[card_key]

        # Получаем ссылку
        file_link = await self._get_card_file_link(card_item)
        if not file_link:
            return None

        # Используем унифицированную функцию из utils
        img = await get_rws_card_image(
            card_id=card_item.id,
            card_name=card_item.display_name,
            file_link=file_link,
            card_width=CARD_WIDTH,
            card_height=CARD_HEIGHT
        )

        if img:
            self._card_images[card_key] = img

        return img

    async def _preload_rws_deck(self):
        """
        Предзагружает все карты RWS с диска в память.
        Вызывается при старте или по требованию.
        """

        # Сначала проверяем что есть на диске
        cached_files = [f for f in os.listdir(RWS_CACHE_DIR) if f.endswith('.png')]

        if len(cached_files) < 78:
            logger.info(f"На диске только {len(cached_files)} RWS-карт, нужно докачать")
            deck = await TarotDeck.objects.filter(id=RWS_DECK_ID).afirst()
            if not deck:
                logger.error(f"Колода {RWS_DECK_ID} не найдена")
                return

            cards = TarotCardItem.objects.prefetch_related('tarot_card').filter(deck=deck)
            async for card in cards:
                await self._get_rws_card_image(card)

        # Загружаем всё в память
        for filename in os.listdir(RWS_CACHE_DIR):
            if filename.endswith('.png'):
                card_key = filename[:-4]
                try:
                    cache_path = get_rws_cache_path(
                        card_id=int(card_key.split('_')[0]),
                        card_name='_'.join(card_key.split('_')[1:])
                    )
                    img = load_rws_image_from_disk(cache_path)
                    if img:
                        self._card_images[card_key] = img
                except Exception as e:
                    logger.warning(f"Не удалось загрузить {filename}: {e}")

        logger.info(f"RWS колода загружена: {len(self._card_images)} карт в памяти")

    async def _create_spread_image(
        self,
        cards: List[Dict],
        username: str = "",
        bot_username: str = ""
    ) -> Optional[io.BytesIO]:
        """
        Создаёт изображение расклада через унифицированную функцию.
        Предварительно загружает изображения карт в card_data["_image"].
        """
        # Предзагружаем изображения карт
        for card_data in cards:
            card_item = card_data.get("card_instance") or card_data.get("card_item")
            if not card_item:
                continue

            card_key = f"{card_item.id}_{card_item.display_name}"
            img = self._card_images.get(card_key)
            if img is None:
                img = await self._get_rws_card_image(card_item)

            card_data["_image"] = img

        # Используем унифицированную функцию из utils
        return await create_full_spread_image(
            cards=cards,
            canvas_width=CANVAS_WIDTH,
            canvas_height=CANVAS_HEIGHT,
            card_width=CARD_WIDTH,
            card_height=CARD_HEIGHT,
            card_margin=CARD_MARGIN,
            canvas_bg=CANVAS_BG,
            username=username,
            bot_username=bot_username
        )

    async def handle_rws_render(self, update: Update, context: CallbackContext):
        """
        Обработчик callback'а для отрисовки расклада в RWS.
        Мгновенно отвечает на callback и запускает рендер в фоне.
        """
        query = update.callback_query
        await query.answer("Собираю расклад...")

        # Извлекаем reading_id из callback_data: "rwsrender_123"
        try:
            _, reading_id = query.data.split("_")
            reading_id = int(reading_id)
        except (ValueError, IndexError):
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Некорректные данные callback.",
                parse_mode=ParseMode.HTML
            )
            return
        
        current_markup = query.message.reply_markup
        if current_markup and current_markup.inline_keyboard:
            new_keyboard = []
            for row in current_markup.inline_keyboard:
                new_row = []
                for button in row:
                    # Пропускаем кнопку с нашим колбэком
                    if button.callback_data == query.data:
                        continue
                    new_row.append(button)
                if new_row:  # Добавляем ряд только если он не пустой
                    new_keyboard.append(new_row)
            
            # Редактируем сообщение с новой клавиатурой
            try:
                if new_keyboard:
                    await query.edit_message_reply_markup(
                        reply_markup=InlineKeyboardMarkup(new_keyboard)
                    )
                else:
                    # Если кнопок не осталось — убираем клавиатуру полностью
                    await query.edit_message_reply_markup(reply_markup=None)
            except Exception as e:
                logger.warning(f"Не удалось обновить клавиатуру: {e}")

        # Запускаем рендер в фоне
        asyncio.create_task(
            self._handle_rws_render_background(update, context, reading_id)
        )


    async def _handle_rws_render_background(self, update: Update, context: CallbackContext, reading_id: int):
        """
        Фоновая задача рендера RWS-расклада.
        При ошибке отправляет новое сообщение, не трогая оригинал.
        """
        chat_id = update.effective_chat.id

        # Загружаем reading из БД
        try:
            reading = await UserReading.objects.aget(id=reading_id)
        except UserReading.DoesNotExist:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Расклад не найден.",
                parse_mode=ParseMode.HTML
            )
            return

        # Получаем список карт из reading
        card_records = reading.card_ids or []
        if not card_records:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ В раскладе нет карт для отрисовки.",
                parse_mode=ParseMode.HTML
            )
            return

        # Формируем cards для рендера
        cards = []
        for item in card_records:
            card_id = str(item.get("id"))
            flipped = item.get("flip", False)

            card_item = await TarotCardItem.objects.prefetch_related('tarot_card', 'files').filter(
                tarot_card__card_id=card_id,
                deck_id=RWS_DECK_ID 
            ).afirst()
            if not card_item:
                logger.warning(f"Карта {card_id} не найдена в БД")
                continue

            cards.append({
                "card_instance": card_item,
                "card_item": card_item,
                "name": card_item.display_name,
                "flipped": flipped,
                "card_id": card_id,
            })

        if not cards:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Не удалось загрузить карты для отрисовки.",
                parse_mode=ParseMode.HTML
            )
            return

        # Создаём изображение
        spread_image = await self._create_spread_image(
            cards=cards,
            username=update.effective_user.username,
            bot_username=context.bot.username
        )

        if not spread_image:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Не удалось создать изображение расклада.",
                parse_mode=ParseMode.HTML
            )
            return

        # Отправляем результат
        try:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=spread_image,
                caption=f"🎨 <b>Классический вид</b>\n\nРасклад в стиле Rider-Waite-Smith",
                parse_mode=ParseMode.HTML,
                reply_to_message_id=reading.message_id,
                read_timeout=60,
                write_timeout=60,
                connect_timeout=30
            )
            reading.has_rws_render = True
            await reading.asave(update_fields=['has_rws_render'])
        except Exception as e:
            logger.error(f"Ошибка отправки изображения: {e}", exc_info=True)


    async def handle_spread(self, update: Update, context: CallbackContext):
        """
        Обработчик /spread и /canvas — мгновенно возвращает управление боту.
        Тяжёлая работа выполняется в фоне.
        """
        msg_text = update.message.text
        user = await self.bot.get_or_create_tg_user(update)
        logger.info(f"Обработка команды /spread с текстом: {msg_text[:100]}")
        

        category = UserReading.ReadingCategory.CANVAS_SPREAD
        is_locked = await self.bot.check_reading_cooldown(update, category)
        if is_locked:
            return
        
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
            options = self.bot.parse_reading_options(msg_text)
            
        deck = await self.bot.get_deck(options.get("deck"), options.get("deck_keyword", None))
        if not deck and options.get("deck"):
            error_msg = self.messages.get_error_message("no_deck")
            await update.message.reply_text(error_msg, parse_mode=ParseMode.HTML)
            return

        logger.info(f"Используемая колода: {deck.id if deck else 'не указана'}")
        
        tech_msg = await update.message.reply_text(
            self.messages.get_initializing(), 
            parse_mode=ParseMode.HTML,
            reply_to_message_id=update.effective_message.message_id
        )
        logger.info(tech_msg)

        cards = await self.bot.get_cards(
            deck_id=deck.id if deck else None,
            counter=options["counter"],
            card_ids=options["card_ids"],
            major=options["major"],
            flip=options['flip'],
            exclude_cards=None,
        )

        if not cards:
            error_msg = self.messages.get_error_message("no_cards")
            await tech_msg.edit_text(error_msg, parse_mode=ParseMode.HTML)
            return
            
        cards_description = [
            self.bot.messages.format_card_name(c["card_instance"].display_name, c['flipped']) 
            for c in cards
        ]

        card_records = [{"id": str(c["card_id"]), "flip": c["flipped"]} for c in cards]

        reading = await self.bot.save_reading(
            user=user,
            message_id=update.effective_message.message_id,
            text=f"{deck.name if deck else 'Дефолтная колода'}: " + ", ".join(
                [await self.bot.format_card_name(c) for c in cards]
            ),
            category=category,
            count=options["counter"],
            deck_id=deck.id if deck else None,
            is_flipped_allowed=options.get('flip', False),
            is_major_only=options.get('major', False),
            card_ids=card_records,
            is_command=any(
                entity.type == MessageEntity.BOT_COMMAND 
                for entity in (update.effective_message.entities or [])
            ),
            original_message_text=update.effective_message.text or "",
        )
        reading.reading_status = UserReading.ReadingStatus.PENDING
        await reading.asave()
        
        description_text = self.messages.format_description(
            deck.name if deck else None, 
            cards_description
        )

        await tech_msg.edit_text(
            f"{self.messages.get_loading()}\n\n{description_text}", 
            parse_mode=ParseMode.HTML
        )

        asyncio.create_task(self._handle_spread_background(update, context, cards, description_text, tech_msg, reading))

    async def _handle_spread_background(self, update: Update, context: CallbackContext, cards, description_text, tech_msg, reading):
        """
        Фоновая задача с полной логикой /spread.
        """

        try:
            # Получаем ссылки на файлы (без скачивания)
            for card_data in cards:
                card_item = card_data["card_instance"]
                file_link = await self._get_card_file_link(card_item)
                if file_link:
                    card_data["file_path"] = file_link
                    logger.info(f"Готов к отправке файл для карты {card_item.id}: {file_link}")
                else:
                    error_msg = self.messages.get_error_message("file_not_found", card_name=card_data["name"])
                    logger.warning(f"Не удалось получить ссылку для карты {card_item.id}")

            await tech_msg.edit_text(
                f"{self.messages.get_rendering()}\n\n{description_text}",
                parse_mode=ParseMode.HTML
            )

            # Используем унифицированную функцию из utils для создания spread-изображения
            spread_image = await utils_create_spread_image(cards)

            await tech_msg.edit_text(
                f"{self.messages.get_uploading()}\n\n{description_text}",
                parse_mode=ParseMode.HTML
            )

            if spread_image:
                await tech_msg.edit_media(
                    media=InputMediaPhoto(media=spread_image, caption=description_text, parse_mode=ParseMode.HTML),
                    read_timeout=60,
                    write_timeout=60,
                    connect_timeout=30
                )
            else:
                raise Exception("create_spread_image вернул None")

            reading.reading_status = UserReading.ReadingStatus.SUCCESS
            await reading.asave()

        except Exception as e:
            if isinstance(e, ValueError):
                logger.error(f"Ошибка валидации: {e}")
                error_msg = self.messages.get_error_message("invalid_options")
            else:
                logger.error(f"Ошибка при обработке команды /spread: {e}", exc_info=True)
                error_msg = self.messages.get_error_message("generic", error_details=str(e)[:100])

            if reading:
                reading.reading_status = UserReading.ReadingStatus.ERROR
                await reading.asave()

            try:
                await tech_msg.edit_text(error_msg, parse_mode=ParseMode.HTML)
            except:
                await update.message.reply_text(error_msg, parse_mode=ParseMode.HTML)