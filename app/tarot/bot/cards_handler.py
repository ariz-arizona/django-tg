import re
import os
from typing import List, Optional, Dict
from collections import Counter
import random

import redis.asyncio as aioredis

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
from telegram.constants import ParseMode, ChatType

from tarot.messages import CardMessages, TAROT_3_TRIGGER
from tarot.models import (
    TarotDeck,
    TarotCardItem,
    TarotCardSticker,
    OraculumDeck,
    OraculumItem,
    UserReading,
)

from server.logger import logger


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

class CardsHandler:
    """Обработчик команды /card и связанных callback'ов."""

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
                filters.Text([TAROT_3_TRIGGER]) & filters.ChatType.PRIVATE,
                self.handle_card
            ),
            MessageHandler(
                filters.COMMAND
                & filters.TEXT
                & (filters.ChatType.PRIVATE | filters.ChatType.GROUPS)
                & filters.Regex(r"^\/card(\d+)?"),
                self.handle_card,
            ),
            CallbackQueryHandler(self.handle_more_button, pattern=r"^more_"),
            
            MessageHandler(
                filters.TEXT
                & filters.ChatType.PRIVATE
                & filters.Regex(r"^(?i)(таро|tarot)\s"),
                self.handle_tarot_text,
            ),
            
            MessageHandler(
                filters.COMMAND
                & filters.TEXT
                & (filters.ChatType.PRIVATE | filters.ChatType.GROUPS)
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
                & filters.Regex(r"^\/tarot(\d+)?"), self.handle_tarot_sticker),
        ]
    
    
    async def handle_card(self, update: Update, context: CallbackContext):
        """
        Обработчик команды /card.
        """
        msg_text = update.message.text
        logger.info(f"Обработка команды /card с текстом: {msg_text[:100]}")

        is_group = update.effective_chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
        category = UserReading.ReadingCategory.TAROT
        if await self.bot.check_reading_cooldown(update, category):
            return

        reading = None
        
        try:
            user = await self.bot.get_or_create_tg_user(update)
            
            # Парсим опции до создания reading
            if msg_text == TAROT_3_TRIGGER:
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
            logger.info(f"Опции расклада разобраны: {options}")

            # Создаём чтение сразу после парсинга опций
            reading = await self.bot.save_reading(
                user=user,
                message_id=update.effective_message.message_id,
                category=category,
                count=options.get("counter", 1),
                is_flipped_allowed=options.get('flip', False),
                is_major_only=options.get('major', False),
                original_query=options.get('original_query', ""),
                is_command=any(
                    entity.type == MessageEntity.BOT_COMMAND 
                    for entity in (update.effective_message.entities or [])
                ),
                original_message_text=msg_text,
            )
            reading.reading_status = UserReading.ReadingStatus.PENDING
            await reading.asave()

            status_message = None
            if not is_group:
                status_message = await update.message.reply_text(
                    self.bot.messages.get_loading(), parse_mode=ParseMode.HTML
                )

            # 1. Получение колоды
            deck = await self.bot.get_deck(options.get("deck"), options.get("deck_keyword", None))
            logger.info(f"Используемая колода ID: {deck.id if deck else 'None'}")

            # 2. Генерация карт
            cards = await self.bot.get_cards(
                deck_id=deck.id if deck else None,
                counter=options.get("counter", 1),
                card_ids=options.get("card_ids"),
                major=options.get("major", False),
                flip=options.get('flip')
            )
            
            # Формируем список словарей для БД
            card_records = [{"id": str(c["card_id"]), "flip": c["flipped"]} for c in cards]
            logger.info(f"Получены карты {card_records}")
            
            result_text = f"{deck.name if deck else 'Дефолтная колода'}: " + \
                        ", ".join([await self.bot.format_card_name(c) for c in cards])

            # ✅ Успех — вставляем данные и меняем статус
            reading.text = result_text
            reading.deck_id = deck.id if deck else None
            reading.card_ids = card_records
            reading.reading_status = UserReading.ReadingStatus.SUCCESS
            await reading.asave()
            logger.info(f"Результат гадания сохранен в БД, ID записи: {reading.id}")

            # 3. Подготовка клавиатуры и отправка
            send_card_kwargs = {
                "reading_id": reading.id,
                "is_group": is_group,
                "send_type": "tarot",
            }
            
            if not is_group and await self.bot.ai_interpret_handler.should_add_ai_button():
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

            # Удаляем статусное сообщение
            if status_message:
                await status_message.delete()

        except Exception as e:
            logger.error(f"Ошибка при обработке команды /card: {e}", exc_info=True)
            # ❌ Ошибка
            if reading:
                reading.reading_status = UserReading.ReadingStatus.ERROR
                await reading.asave()
            await update.message.reply_text(
                self.bot.messages.get_error_message("generic", error_details=str(e)),
                parse_mode=ParseMode.HTML
            )

    async def handle_more_button(self, update: Update, context: CallbackContext):
        query = update.callback_query
        await query.answer()
        logger.info(f"Получен callback-запрос: {query.data}")

        try:
            _, reading_id = query.data.split("_")
            user = await self.bot.get_or_create_tg_user(update)

            # Ищем существующий расклад
            reading = await UserReading.objects.filter(id=reading_id, user=user).afirst()

            # УСЛОВИЕ: Если расклада нет — чистим кнопки и выходим
            if not reading:
                logger.warning(f"Расклад {reading_id} не найден.")
                await query.edit_message_text(
                    self.bot.messages.get_error_message("no_cards")
                )
                await query.edit_message_reply_markup(reply_markup=None)
                return

            # Статус: начинаем обработку
            reading.reading_status = UserReading.ReadingStatus.PENDING
            await reading.asave()

            # === Подготовка данных ===
            exclude_cards = [str(item.get("id") if isinstance(item, dict) else item) for item in (reading.card_ids or [])]
            logger.info(f"Получаем карты с major {bool(reading.is_major_only)} flip {bool(reading.is_flipped_allowed)}. Исключаем: {exclude_cards}")

            new_card = await self.bot.get_cards(
                deck_id=reading.deck_id,
                counter=1,
                exclude_cards=exclude_cards,
                major=bool(reading.is_major_only),
                flip=bool(reading.is_flipped_allowed)
            )

            if not new_card:
                reading.reading_status = UserReading.ReadingStatus.ERROR
                await reading.asave()
                await query.edit_message_text(
                    self.bot.messages.get_error_message("no_cards")
                )
                await query.edit_message_reply_markup(reply_markup=None)
                return

            # === Обновление записи ===
            new_card_text = ", ".join([await self.bot.format_card_name(c) for c in new_card])
            new_card_data = [{"id": c["card_id"], "flip": c["flipped"]} for c in new_card]
            logger.info(f"Выбраны новые карты: {[c['name'] + ' ' + str(c['flipped']) for c in new_card]}")

            reading.text = f"{reading.text}, {new_card_text}"
            reading.count += 1
            reading.card_ids.extend(new_card_data)
            reading.reading_status = UserReading.ReadingStatus.SUCCESS
            await reading.asave()

            # === Отправка результата ===
            send_card_kwargs = {"reading_id": reading.id, "send_type": "tarot"}
            if await self.bot.ai_interpret_handler.should_add_ai_button():
                send_card_kwargs["add_ai_button"] = "🔮 Растолковать расклад (ИИ)"

            await self.send_card(update, new_card, **send_card_kwargs)

            # Очистка кнопок у старого сообщения
            await query.edit_message_reply_markup(reply_markup=None)

        except Exception as e:
            logger.error(f"Ошибка при обработке добора карты: {e}", exc_info=True)
            # ❌ Ошибка
            if reading:
                reading.reading_status = UserReading.ReadingStatus.ERROR
                await reading.asave()
            await query.edit_message_text(
                self.bot.messages.get_error_message("generic", error_details=str(e)),
                parse_mode=ParseMode.HTML
            )

    async def handle_oraculum(self, update: Update, context: CallbackContext):
        msg_text = update.message.text
        logger.info(f"Обработка команды /oraculum: {msg_text[:100]}")

        is_group = update.effective_chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
        category = UserReading.ReadingCategory.ORACLE
        if await self.bot.check_reading_cooldown(update, category):
            return

        reading = None
        try:
            user = await self.bot.get_or_create_tg_user(update)
            options = self.bot.parse_reading_options(msg_text)

            reading = await self.bot.save_reading(
                user=user,
                message_id=update.effective_message.message_id,
                category=category,
                count=options.get("counter", 1),
                is_flipped_allowed=options.get('flip', False),
                is_command=True,
                original_message_text=msg_text,
            )
            reading.reading_status = UserReading.ReadingStatus.PENDING
            await reading.asave()

            status_message = None
            if not is_group:
                status_message = await update.message.reply_text(
                    self.bot.messages.get_loading(), parse_mode=ParseMode.HTML,
                )
            
            # 1. Получение колоды
            deck = await self.bot.get_deck(options.get("deck"), options.get("deck_keyword", None), 'oraculum')
            
            # 2. Получение карт
            cards = await self.bot.get_oraculum_cards(
                deck.id if deck else None, 
                options.get("counter", 1), 
                [],
                options.get('flip', False)
            )
            
            # 3. Обновление записи с данными
            card_records = [{"id": str(c["card_id"]), "flip": c["flipped"]} for c in cards]
            result_text = f"{deck.name if deck else 'Дефолтный оракул'}: " + \
                        ", ".join([await self.bot.format_card_name(c) for c in cards])

            reading.text = result_text
            reading.deck_id = deck.id if deck else None
            reading.card_ids = card_records
            reading.reading_status = UserReading.ReadingStatus.SUCCESS
            await reading.asave()
            logger.info(f"Результат оракула сохранен в БД, ID: {reading.id}")

            # 4. Отправка карт
            send_card_kwargs = {
                "reading_id": reading.id,
                "is_group": is_group,
                "send_type": "oracle",
            }

            await self.send_card(
                update,
                cards,
                **send_card_kwargs
            )
            
            logger.info(f"Карты оракула успешно отправлены: {[c['name'] for c in cards]}")

            # Удаляем статусное сообщение
            if status_message:
                await status_message.delete()

        except Exception as e:
            logger.error(f"Ошибка при обработке команды /oraculum: {e}", exc_info=True)
            if reading:
                reading.reading_status = UserReading.ReadingStatus.ERROR
                await reading.asave()
            await update.message.reply_text(
                self.bot.messages.get_error_message("generic", error_details=str(e)),
                parse_mode=ParseMode.HTML
            )

            
    async def handle_moreoracle_button(self, update: Update, context: CallbackContext):
        query = update.callback_query
        await query.answer()
        logger.info(f"Получен callback-запрос: {query.data}")
        
        reading = None
        try:
            # Получаем reading_id напрямую из callback_data
            _, reading_id = query.data.split("_")
            
            # 1. Поиск расклада по ID
            reading = await UserReading.objects.filter(id=reading_id).afirst()
            
            if not reading:
                logger.warning(f"Расклад с ID {reading_id} не найден.")
                await query.edit_message_text(
                    self.bot.messages.get_error_message("no_cards")
                )
                return

            # Статус: начинаем обработку
            reading.reading_status = UserReading.ReadingStatus.PENDING
            await reading.asave()
            
            # 2. Подготовка исключений (извлекаем card_ids из БД)
            exclude_cards = [
                str(item.get("id") if isinstance(item, dict) else item) 
                for item in (reading.card_ids or [])
            ]
            logger.info(f"Получаем карты оракула с major {bool(reading.is_major_only)} flip {bool(reading.is_flipped_allowed)}. Исключаем: {exclude_cards}")

            # 3. Генерация карты (используем настройки из самого расклада)
            new_card = await self.bot.get_oraculum_cards(
                deck_id=reading.deck_id,
                counter=1,
                exclude_cards=exclude_cards,
                flip=reading.is_flipped_allowed
            )

            if not new_card:
                reading.reading_status = UserReading.ReadingStatus.ERROR
                await reading.asave()
                await query.edit_message_text(
                    self.bot.messages.get_error_message("no_cards")
                )
                return

            # 4. Обновление БД
            new_card_data = [{"id": str(c["card_id"]), "flip": c["flipped"]} for c in new_card]
            new_card_text = ", ".join([await self.bot.format_card_name(c) for c in new_card])
            logger.info(f"Выбраны новые карты: {[c['name'] + ' ' + str(c['flipped']) for c in new_card]}")
            
            reading.text = f"{reading.text}, {new_card_text}"
            reading.count += 1
            reading.card_ids.extend(new_card_data)
            reading.reading_status = UserReading.ReadingStatus.SUCCESS
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
            if reading:
                reading.reading_status = UserReading.ReadingStatus.ERROR
                await reading.asave()
            await query.edit_message_text(
                self.bot.messages.get_error_message("generic", error_details=str(e)),
                parse_mode=ParseMode.HTML,
            )

    async def handle_tarot_text(self, update: Update, context: CallbackContext):
        msg_text = update.message.text
        logger.info(f"Обработка текстовой строки ТАРО с текстом: {msg_text[:100]}")

        try:
            # 1. Парсим опции
            options = self.bot.parse_text_reading_options(msg_text)
            
            # 2. Запрашиваем колоды (может быть список)
            decks = await self.bot.get_deck(
                deck_id=options.get("deck"),
                deck_keyword=options.get("deck_keyword"),
                deck_type="tarot",
                return_all=True,
            )
            
            # 3. Формируем базовую команду
            command_parts = ["/card"]
            if options["counter"] > 1:
                command_parts[0] = command_parts[0] + str(options["counter"])
            if options["flip"]:
                command_parts.append("flip")
            if options["major"]:
                command_parts.append("major")
            
            base_command = "_".join(command_parts)
            
            info_text = self.bot.messages.get_deck_search_result(
                decks=decks,
                keyword=options.get('deck_keyword', ''),
                base_command=base_command
            )
            
            await update.message.reply_text(info_text, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Ошибка при обработке команды /card: {e}", exc_info=True)
            await update.message.reply_text(
                self.bot.messages.get_error_message("generic", error_details=str(e)),
                parse_mode=ParseMode.HTML
            )
    
    async def send_card(self, update: Update, cards, **kwargs):
        reading_id = kwargs.get("reading_id")
        send_type = kwargs.get("send_type") # 'tarot' или 'oracle'
        is_group = kwargs.get("is_group", False)
        params = {"disable_web_page_preview": True}
        

        # 1. Отправка фото        
        if is_group:
            # Для групп - отправляем без reply
            mg = await update.effective_chat.send_media_group(
                [InputMediaPhoto(c["img_id"], await self.bot.format_card_name(c)) for c in cards],
                message_thread_id=update.effective_message.message_thread_id  # для топиков
            )
        else:
            # Для личных чатов - с reply
            mg = await update.effective_message.reply_media_group(
                [InputMediaPhoto(c["img_id"], await self.bot.format_card_name(c)) for c in cards],
                reply_to_message_id=update.effective_message.message_id,
            )
            

        reply_markup = []
        text = []

        # 2. Логика для ТАРО
        if send_type == 'tarot':
            reading = await UserReading.objects.aget(id=reading_id)
            current_deck = await TarotDeck.objects.aget(id=reading.deck_id)
            
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
                    "flipped": flip_map.get(card_id_str, False)
                }
            
            all_cards = [cards_dict[cid] for cid in order_list if cid in cards_dict]
            
            total_query = TarotCardItem.objects.filter(deck_id=current_deck.id)
            if reading.is_major_only:
                total_query = total_query.filter(tarot_card__is_major=True)
            total_cards = await total_query.acount()
            can_draw_query = total_query.exclude(tarot_card__card_id__in=order_list)
            can_draw = await can_draw_query.aexists()
            current_count = len(all_cards)

            # Формируем описание карт как список строк
            cards_description = [
                self.bot.messages.format_card_name(c["card_instance"].display_name, c['flipped']) 
                for c in all_cards
            ]
            
            # Статистика по колоде
            stats_str = self.bot.messages.get_deck_stats(current_count, total_cards)
            
            # Текст для больших раскладов
            try_all_str = None
            if current_count > 10:
                flag = ""
                if reading.is_flipped_allowed:
                    flag += "_flip"
                if reading.is_major_only:
                    flag += "_major"
                try_all_str = self.bot.messages.get_try_all_deck(
                    deck_id=current_deck.slug,
                    flag=flag
                )
                
            text = []
            if is_group and update.effective_user.username and not update.effective_user.is_bot:
                text.append(self.bot.messages.format_user_mention(update.effective_user))
            
            text.append(self.bot.messages.format_description(
                deck_name=current_deck.name,
                deck_link=current_deck.link,
                cards_description=cards_description,
                stats_str=f'<i>Расклад от @{update._bot.username}</i>' if is_group else stats_str,
                try_all_str=try_all_str
            ))            
            
            base_cmd = f"/card{min(reading.count, 10) if reading.count > 1 else ''}"
            repeat_cmd = self.bot.messages.build_deck_command(
                base_command=base_cmd,
                deck_slug=current_deck.slug,
                major=reading.is_major_only,
                flip=reading.is_flipped_allowed
            )
            text.append(self.bot.messages.get_repeat_command(repeat_cmd))
            
            if not is_group:
                spread_summary = self.bot.messages.get_spread_summary(
                    deck_name=current_deck.name if current_deck else "Стандартная колода",
                    count=reading.count,
                    is_flipped=reading.is_flipped_allowed,
                    is_major_only=reading.is_major_only,
                    seo_tags=current_deck.seo_tags if current_deck else None
                )
                text.append(f"\n📋 <code>{spread_summary}</code>")
                
            params["parse_mode"] = ParseMode.HTML
            
            row = [InlineKeyboardButton("Еще карту", callback_data=f"more_{reading_id}")] if can_draw else []
            row.append(InlineKeyboardButton(f"Трактовка карт ({len(all_cards)})", callback_data=f"desc_{reading_id}"))
            reply_markup.append(row)
            
            if current_count <= 16:
                reply_markup.append([InlineKeyboardButton(text="🎨 Классический вид", callback_data=f"rwsrender_{reading_id}")])
            
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
                id__in=order_list
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
            
            card_names = [
                self.bot.messages.format_card_name(c['name'], c['flipped']) 
                for c in all_cards
            ]
            stats_str = self.bot.messages.get_deck_stats(current_count, total_cards)
            
            text = [self.bot.messages.format_description(
                deck_name=current_deck.name,
                deck_description=current_deck.description, 
                cards_description=card_names,
                stats_str=stats_str
            )]
            
            params["parse_mode"] = ParseMode.HTML
            
            if current_count < total_cards:
                reply_markup.append([InlineKeyboardButton("Еще карту", callback_data=f"moreoracle_{reading_id}")])

        # 4. Финальная отправка
        params["text"] = "\n".join(text)
        
        if not is_group:
            reply_target = update.effective_message.reply_to_message
            params["reply_to_message_id"] = (
                reply_target.message_id if reply_target else update.effective_message.message_id
            )
    
        if is_group:
            reply_markup = []
        
        if reply_markup:
            params["reply_markup"] = InlineKeyboardMarkup(reply_markup)

        if is_group:
            await update.effective_chat.send_message(
                text=params.pop("text"),
                **params
            )
        else:
            await update.effective_message.reply_text(**params)
        
    async def handle_tarot_sticker(self, update: Update, context: CallbackContext):
        """
        Обработчик команды /tarot.
        Случайная карта из стикеров TarotCardSticker, как в рунах.
        """
        msg_text = update.message.text
        logger.info(f"Обработка команды /tarot: {msg_text[:100]}")

        category = UserReading.ReadingCategory.TAROT_STICKER
        if await self.bot.check_reading_cooldown(update, category):
            return

        reading = None
        try:
            user = await self.bot.get_or_create_tg_user(update)
            options = self.bot.parse_reading_options(msg_text)

            reading = await self.bot.save_reading(
                user=user,
                message_id=update.effective_message.message_id,
                category=category,
                count=1,
                is_flipped_allowed=options.get('flip', False),
                is_command=True,
                original_message_text=msg_text,
            )
            reading.reading_status = UserReading.ReadingStatus.PENDING
            await reading.asave(update_fields=['reading_status'])
            
            card_ids = options.get("card_ids", []) 

            # Получаем все доступные card_id через связь tarot_card
            available_card_qs = TarotCardSticker.objects.prefetch_related('tarot_card').values_list(
                'tarot_card__card_id', flat=True
            )
            
            available_card_ids = []
            async for q in available_card_qs:
                available_card_ids.append(q)
                
            if not available_card_ids:
                raise ValueError("Стикеры Таро не настроены")

            # Определяем ID карты: используем переданный или выбираем случайный
            if card_ids and len(card_ids) > 0:
                requested_card_id = str(card_ids[0])
                if requested_card_id in available_card_ids:
                    selected_card_id = requested_card_id
                else:
                    selected_card_id = random.choice(available_card_ids)
            else:
                selected_card_id = random.choice(available_card_ids)

            # Получаем стикер по card_id из tarot_card
            random_sticker = await TarotCardSticker.objects.prefetch_related('tarot_card').aget(tarot_card__card_id=selected_card_id)

            inverted = options.get('flip', False) and random.choice([True, False])

            # Сохраняем в reading
            reading.card_ids = [{"id": selected_card_id, "inverted": inverted}]
            reading.text = self.bot.messages.format_card_name(random_sticker.tarot_card.name, inverted)
            
            await reading.asave(update_fields=['text', 'card_ids'])
            
            reading.reading_status = UserReading.ReadingStatus.SUCCESS
            await reading.asave(update_fields=['reading_status'])

            # Отправка
            # await update.message.reply_text(reading.text, parse_mode=ParseMode.HTML)
            await update.message.reply_sticker(random_sticker.sticker, reply_to_message_id=reading.message_id)

            logger.info(f"Отправлен стикер {random_sticker.tarot_card.name}")

        except Exception as e:
            logger.error(f"Ошибка при обработке /tarot: {e}", exc_info=True)
            if reading:
                reading.reading_status = UserReading.ReadingStatus.ERROR
                await reading.asave(update_fields=['reading_status'])
            await update.message.reply_text(
                self.bot.messages.get_error_message("generic", error_details=str(e)),
                parse_mode=ParseMode.HTML
            )