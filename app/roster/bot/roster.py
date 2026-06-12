# roster/bot.py
import random
import os
import redis
from datetime import timedelta
from typing import List

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from telegram.ext import (
    CommandHandler,
    CallbackQueryHandler,
    CallbackContext,
    filters,
)
from telegram.constants import ParseMode

from django.utils.timezone import now
from django.db.models import Count, Q

from tg_bot.bot.abstract import AbstractBot
from tg_bot.models import TgUser, Bot, BotFile

from roster.models.team import Season, Team, Card
from roster.models.roll import UserRoll, RosterUser
from roster.models.tech import RollLimit, BotText

from server.logger import logger

redis_client = redis.StrictRedis(
    host=os.getenv("REDIS_HOST"),
    port=os.getenv("REDIS_PORT"),
    db=3,
    decode_responses=True,
)


class GachaBot(AbstractBot):
    DEFAULT_LIMITS = {
        "cooldown": 60,
        "bihourly": 5,
        "daily": 10,
    }
    def __init__(self):
        self.handlers = self.get_handlers()

    def get_handlers(self):
        return [
            CommandHandler("start", self.handle_start, filters.ChatType.PRIVATE),
            CommandHandler("me", self.handle_me, filters.ChatType.PRIVATE),
            CommandHandler(["roll", "get"], self.handle_roll, filters.ChatType.PRIVATE),
            CallbackQueryHandler(
                self.handle_roll_album, pattern=r"^rollimg_\d+(_\d+){5}$"
            ),
        ]

    # ─── Вспомогательные методы ─────────────────────────────────────

    async def get_or_create_user(self, tg_user) -> TgUser:
        """Находит или создаёт TgUser по данным Telegram."""
        user, _ = await TgUser.objects.aget_or_create(
            tg_id=tg_user.id,
            defaults={
                "username": tg_user.username,
                "first_name": tg_user.first_name,
                "last_name": tg_user.last_name,
                "language_code": tg_user.language_code,
                "is_bot": tg_user.is_bot,
            },
        )
        # 2. Находим или создаем гача-профиль для этого пользователя
        roster_user, _ = await RosterUser.objects.aget_or_create(
            user=user,
            defaults={
                "is_premium": False,
                "description": "",
            }
        )
        
        # Пред-заполняем связь в объекте (кэшируем), чтобы Django не ходил 
        # в базу повторно, когда ты вызовешь user.roster_user
        user.roster_user = roster_user
        return user

    async def get_active_season(self) -> Season | None:
        """Возвращает активный сезон или None."""
        try:
            bot = await self.get_bot_instance()
            return await Season.objects.filter(
                is_active=True,
                end_date__gte=now(),
                bot=bot
            ).afirst()
        except Exception:
            return None

    async def get_bot_instance(self) -> Bot | None:
        """Возвращает инстанс бота из конфига (заглушка, донастроишь под себя)."""
        try:
            return await Bot.objects.filter(id=self.app_bot_id).afirst()
        except Exception:
            return None
        
    async def get_roll_limits(
        self, 
        tg_user, 
        limit_types: list[str] | str, 
    ) -> dict[str, int]:
        """
        Принимает список лимитов (или один лимит) и возвращает словарь {тип_лимита: значение}.
        Делает ВСЕГО ОДИН запрос за пользователем и ОДИН запрос за всеми лимитами сразу.
        """
        # Переводим в список, если пришла одна строка
        if isinstance(limit_types, str):
            limit_types = [limit_types]
            
        # 1. Один запрос за юзером (создание или получение)
        bot = await self.get_bot_instance()
        user = await self.get_or_create_user(tg_user)
        is_premium = user.roster_user.is_premium

        # 2. Один запрос в базу: выгребаем сразу ВСЕ нужные лимиты для этого бота
        # И для премиума, и обычные (чтобы сделать фоллбек в памяти, а не в БД)
        limits_queryset = RollLimit.objects.filter(
            bot=bot,
            limit_type__in=limit_types
        )
        
        db_limits = [limit async for limit in limits_queryset]
        
        # Собираем финальный результат
        result = {}
        for l_type in limit_types:
            # Ищем в подгруженном списке премиум-лимит
            premium_obj = next((l for l in db_limits if l.limit_type == l_type and l.is_premium), None)
            # Ищем обычный лимит
            regular_obj = next((l for l in db_limits if l.limit_type == l_type and not l.is_premium), None)
            
            if is_premium and premium_obj:
                result[l_type] = premium_obj.value
            elif regular_obj:
                result[l_type] = regular_obj.value
            else:
                result[l_type] = self.DEFAULT_LIMITS.get(l_type, 5)
                
        return result

    # ─── /start ──────────────────────────────────────────────────────

    async def handle_start(self, update: Update, context: CallbackContext):
        """Приветствие и краткая справка."""
        tg_user = update.effective_user
        user = await self.get_or_create_user(tg_user)
        bot = await self.get_bot_instance()
        
        limits = await self.get_roll_limits(
            limit_types=["daily"], 
            tg_user=tg_user,
        )

        try:
            text_obj = await BotText.objects.aget(bot=bot, text_type="start")
            text = text_obj.text
        except BotText.DoesNotExist:
            text = (
                "🦸 Привет, {first_name}!\n\n"
                "Добро пожаловать в Marvel Gacha.\n\n"
                "🎲 <b>/roll</b> — вытянуть случайную карту ({daily_limit} в день)\n"
                "📊 <b>/me</b> — посмотреть свой прогресс за неделю\n\n"
                "Собери всех героев до конца сезона!"
            )

        text = text.format(
            first_name=user.first_name or 'герой',
            daily_limit=limits['daily'],
        )
        await update.message.reply_html(text)

    # ─── /roll (он же /get) ──────────────────────────────────────────

    async def handle_roll(self, update: Update, context: CallbackContext):
        """Случайный бросок карты."""
        tg_user = update.effective_user
        user = await self.get_or_create_user(tg_user)
        bot = await self.get_bot_instance()
        season = await self.get_active_season()
        

        # Проверки
        if not season:
            await update.message.reply_text(
                "⏳ Сейчас нет активного сезона. Загляни позже!"
            )
            return

        # Лимит: 3 броска за последние 24 часа
        day_ago = now() - timedelta(hours=24)
        rolls_today = await UserRoll.objects.filter(
            user=user,
            bot=bot,
            rolled_at__gte=day_ago,
        ).acount()

        redis_key = f"roll:{user.tg_id}:{bot.id}"

        # ─── Загрузка лимитов из базы ────────────────────────────
        limits = await self.get_roll_limits(
            tg_user, 
            ["cooldown", "daily", "bihourly"]
        )
            
        # Кулдаун (секунды между бросками) — через Redis
        cooldown_sec = limits["cooldown"]
        if redis_client.exists(redis_key):
            ttl = redis_client.ttl(redis_key)
            await update.message.reply_text(
                f"⏳ Подожди {ttl} сек. перед следующим броском!"
            )
            return

        # Дневной лимит
        daily_limit = limits["daily"]
        day_ago = now() - timedelta(hours=24)
        rolls_today = await UserRoll.objects.filter(
            user=user,
            bot=bot,
            rolled_at__gte=day_ago,
        ).acount()

        if rolls_today >= daily_limit:
            await update.message.reply_text(
                f"⛔ Дневной лимит исчерпан: {rolls_today}/{daily_limit}.\n"
                "Попробуй снова позже!"
            )
            return

        # Двухчасовой лимит
        bihourly_limit = limits["bihourly"]
        two_hours_ago = now() - timedelta(hours=2)
        rolls_bihourly = await UserRoll.objects.filter(
            user=user,
            bot=bot,
            rolled_at__gte=two_hours_ago,
        ).acount()

        if rolls_bihourly >= bihourly_limit:
            await update.message.reply_text(
                f"⛔ Лимит за 2 часа исчерпан: {rolls_bihourly}/{bihourly_limit}.\n"
                "Попробуй снова позже!"
            )
            return

        # Выбор случайной карты из активного сезона (с весом по звёздам)
        all_cards: List[Card] = [
            card
            async for card in Card.objects.filter(
                team__season=season,
            ).select_related("team")
        ]

        if not all_cards:
            await update.message.reply_text("🃏 В сезоне пока нет карт.")
            return
        
        # Рассчитываем веса
        weights = [int(60 / card.stars) for card in all_cards]

        # Логируем информацию о картах и их весах
        logger_info = [
            f"ID: {card.id} | Name: {card.name} | Stars: {card.stars} | Weight: {w}"
            for card, w in zip(all_cards, weights)
        ]
        logger.info(f"Сформирован пул карт для выбора:\n" + "\n".join(logger_info))

        # random.choices сам распределит вероятности согласно этим весам
        picked_card = random.choices(all_cards, weights=weights, k=1)[0]

        logger.info(f"Выбрана карта: ID {picked_card.id}, {picked_card.name}")

        # Сохраняем бросок
        await UserRoll.objects.acreate(
            user=user,
            bot=bot,
            card=picked_card,
            season=season,
        )

        # Собираем коллекцию пользователя за сезон
        collected_cards = set()
        async for roll in UserRoll.objects.filter(
            user=user,
            season=season,
        ).select_related("card"):
            collected_cards.add(roll.card_id)

        total_cards = len(all_cards)
        unique_collected = len(set(collected_cards))

        # Собираем все команды сезона для кнопок
        teams = []
        async for team in (
            Team.objects.filter(season=season)
            .order_by("name")
            .prefetch_related("cards")
        ):
            teams.append(team)

        # Кнопки: по одной на команду, ведут на заглушечный колбэк
        keyboard = []
        for team in teams:
            cards = [card async for card in team.cards.all().order_by("id")]
            slots = []
            
            # Считаем, сколько карт этой команды уже открыто у пользователя
            team_total = len(cards)
            team_collected = sum(1 for card in cards if card.id in collected_cards)
            
            # Формируем красивый статус для названия кнопки
            if team_collected == team_total and team_total > 0:
                status_emoji = "✅ "  # Фулл сет собран
            else:
                status_emoji = "🃏 "  # В процессе сборки
                
            button_text = f"{status_emoji}{team.name} ({team_collected}/{team_total})"

            # Твоя логика слотов для callback_data (ограничение до 10 карт)
            slots = []
            for card in cards[:10]:
                slots.append(str(card.id) if card.id in collected_cards else "0")

            callback_data = f"rollimg_{team.id}_" + "_".join(slots)
            keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])

        try:
            text_obj = await BotText.objects.aget(bot=bot, text_type="roll")
            text = text_obj.text
        except BotText.DoesNotExist:
            text = (
                "🎲 Ты вытянул карту!\n\n"
                "{stars} <b>{name}</b>\n"
                "🛡 Команда: {team}\n"
                "📝 {description}\n\n"
                "📊 Прогресс: {unique_collected}/{total_cards} уникальных карт"
            )

        text = text.format(
            stars='⭐' * picked_card.stars,
            name=picked_card.name,
            team=picked_card.team.name,
            description=picked_card.description or 'Описание пока не добавлено.',
            unique_collected=unique_collected,
            total_cards=total_cards,
        )
        
        file_id = await picked_card.aget_image_id(bot.id)
        await update.message.reply_photo(
            photo=file_id,
            caption=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )

        redis_client.setex(redis_key, cooldown_sec, card.id)

    async def handle_roll_album(self, update: Update, context: CallbackContext):
        """Показывает альбом команды, где открытые карты = image, скрытые = image_hidden."""
        query = update.callback_query
        await query.answer()

        bot = await self.get_bot_instance()
        if not bot:
            await query.edit_message_text("🤖 Бот не настроен.")
            return

        # Парсим колбэк: rollimg_TEAMID_SLOT1_SLOT2_SLOT3_SLOT4_SLOT5
        parts = query.data.split("_")
        team_id = int(parts[1])
        slots = [int(x) for x in parts[2:7]]  # 5 слотов

        # Получаем команду и её карты
        try:
            team = await Team.objects.filter(id=team_id).afirst()
        except Exception:
            await query.edit_message_text("🫥 Команда не найдена.")
            return

        if not team:
            await query.edit_message_text("🫥 Команда не найдена.")
            return

        cards = []
        async for card in team.cards.all().order_by("id"):
            cards.append(card)

        # Формируем альбом
        media_group = []
        for i, card in enumerate(cards):
            is_collected = slots[i] != 0

            if is_collected:
                file_id = await card.aget_image_id(bot.id)
            else:
                file_id = await team.aget_file_id(bot.id)
                        
            try:
                text_obj = await BotText.objects.aget(bot=bot, text_type="caption")
                caption_template = text_obj.text
            except BotText.DoesNotExist:
                caption_template = (
                    "{stars} <b>{name}</b>\n"
                    "{collected_status}"
                )

            caption = caption_template.format(
                stars='⭐' * card.stars,
                name=card.name,
                collected_status='✅ Собрано' if is_collected else '❓ Не собрано',
            )

            media_group.append(
                InputMediaPhoto(
                    media=file_id,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                )
            )

        # Убираем кнопки и шлём альбом
        await query.edit_message_reply_markup(reply_markup=None)

        if media_group:
            await context.bot.send_media_group(
                chat_id=query.message.chat_id,
                media=media_group,
            )

    # ─── /me ─────────────────────────────────────────────────────────

    async def handle_me(self, update: Update, context: CallbackContext):
        """Показывает прогресс пользователя за текущий сезон."""
        tg_user = update.effective_user
        user = await self.get_or_create_user(tg_user)
        bot = await self.get_bot_instance()
        season = await self.get_active_season()

        if not season:
            await update.message.reply_text("⏳ Сейчас нет активного сезона.")
            return
        
        # Гарантируем создание/получение пользователя со связью RosterUser
        user = await self.get_or_create_user(tg_user)
        is_premium = user.roster_user.is_premium

        # ─── 1. Сбор лимитов и состояния бросков ──────────────────────
        limits = await self.get_roll_limits(tg_user, ["cooldown", "daily"])
        cooldown_sec = limits.get("cooldown")
        daily_limit = limits.get("daily")
        
        # Сколько уже сделано за последние 24 часа
        day_ago = now() - timedelta(hours=24)
        rolls_today = await UserRoll.objects.filter(
            user=user,
            bot=bot,
            rolled_at__gte=day_ago,
        ).acount()
        
        available_rolls = max(0, daily_limit - rolls_today)
        
        # Проверяем текущий кулдаун в Redis
        redis_key = f"roll:{user.tg_id}:{bot.id}"
        ttl = redis_client.ttl(redis_key) if redis_client.exists(redis_key) else 0
        
        if available_rolls == 0:
            cooldown_status = "❌ Попытки на сегодня исчерпаны. Жди обновления лимитов!\n"
        elif ttl > 0:
            cooldown_status = f"🔄 Следующий бросок через: {ttl} сек\n"
        else:
            cooldown_status = "✅ Готов к броску!\n"
            
        premium_status = "Да" if is_premium else "Нет ( /buy_premium )"

        # ─── 2. Сбор статистики по картам и командам ──────────────────
        rolls = UserRoll.objects.filter(
            user=user,
            season=season,
        ).select_related("card", "card__team")

        teams_dict: dict = {}
        async for roll in rolls:
            team_name = roll.card.team.name
            if team_name not in teams_dict:
                teams_dict[team_name] = set()
            teams_dict[team_name].add(roll.card.name)

        all_teams = Team.objects.filter(season=season).prefetch_related("cards")
        all_cards_count = 0
        collected_count = 0
        teams_lines = []

        async for team in all_teams:
            team_cards = [card async for card in team.cards.all()]
            collected = teams_dict.get(team.name, set())
            team_collected = len(collected)
            team_total = len(team_cards)

            all_cards_count += team_total
            collected_count += team_collected

            emoji = "✅" if team_collected == team_total and team_total > 0 else "🔲"
            card_list = ", ".join(sorted(collected)) if team_collected > 0 else "—"

            teams_lines.append(f"{emoji} <b>{team.name}</b> ({team_collected}/{team_total})\n   {card_list}")

        # Сроки сезона
        days_left = (season.end_date - now()).days
        days_text = f"{days_left} дн." if days_left > 0 else "сегодня!"

        # ─── 3. Сборка и отправка текста ──────────────────────────────
        try:
            text_obj = await BotText.objects.aget(bot=bot, text_type="me")
            text_template = text_obj.text
        except BotText.DoesNotExist:
            # Если в базе нет, берем дефолтный хардкод
            text_template = (
                "📊 <b>Твой прогресс — {season_name}</b>\n\n"
                "{teams_progress}\n\n"
                "🎯 <b>Итого: {collected_count}/{all_cards_count} карт</b>\n\n"
                "⚡ <b>Броски (попытки):</b>\n"
                "🟢 Доступно сегодня: {available_rolls}/{daily_limit} (кулдаун: {cooldown_sec} сек)\n"
                "{cooldown_status}"
                "💎 Премиум: {premium_status}\n\n"
                "⏳ До конца сезона: {days_text}"
            )

        final_text = text_template.format(
            season_name=season.name,
            teams_progress="\n".join(teams_lines),
            collected_count=collected_count,
            all_cards_count=all_cards_count,
            available_rolls=available_rolls,
            daily_limit=daily_limit,
            cooldown_sec=cooldown_sec,
            cooldown_status=cooldown_status,
            premium_status=premium_status,
            days_text=days_text
        )

        await update.message.reply_html(final_text)
