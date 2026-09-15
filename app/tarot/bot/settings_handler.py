from datetime import time as dt_time
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, CallbackQueryHandler, CallbackContext, filters
from telegram.constants import ParseMode

from tarot.messages import SettingsMessages  
from tarot.models import TarotUser

ST_PREFIX = "st_"
TIMES = ["06:00", "09:00", "12:00"]


class SettingsHandler:
    def __init__(self, bot_instance):
        self.bot = bot_instance
        self.messages = SettingsMessages()

    def get_handlers(self):
        return [
            CommandHandler("settings", self.handle_settings, filters.ChatType.PRIVATE),
            CallbackQueryHandler(self.handle_callback, pattern=rf"^{ST_PREFIX}"),
        ]

    async def _get_tarot_user(self, tg_user):
        obj, _ = await TarotUser.objects.aget_or_create(
            user=tg_user,
            defaults={
                "nsfw_allowed": None,
                "nsfw_spoiler": True,
                "daily_enabled": False,
                "daily_time": None,
            },
        )
        return obj

    def _build_keyboard(
        self,
        nsfw: Optional[bool],
        spoiler: bool,
        daily: bool,
        time: Optional[str],
    ):
        m = self.messages

        # 18+
        row_nsfw = [
            InlineKeyboardButton(
                f"{m.CHECK if nsfw is True else ''}{m.NSFW_YES}",
                callback_data=f"{ST_PREFIX}nsfw_yes",
            ),
            InlineKeyboardButton(
                f"{m.CHECK if nsfw is False else ''}{m.NSFW_NO}",
                callback_data=f"{ST_PREFIX}nsfw_no",
            ),
        ]

        # Спойлер
        row_spoiler = [
            InlineKeyboardButton(
                f"{m.CHECK if spoiler else ''}{m.SPOILER_HIDE}",
                callback_data=f"{ST_PREFIX}spoiler_yes",
            ),
            InlineKeyboardButton(
                f"{m.CHECK if not spoiler else ''}{m.SPOILER_SHOW}",
                callback_data=f"{ST_PREFIX}spoiler_no",
            ),
        ]

        # Daily
        row_daily = [
            InlineKeyboardButton(
                f"{m.CHECK if daily else ''}{m.DAILY_ON}",
                callback_data=f"{ST_PREFIX}daily_on",
            ),
            InlineKeyboardButton(
                f"{m.CHECK if not daily else ''}{m.DAILY_OFF}",
                callback_data=f"{ST_PREFIX}daily_off",
            ),
        ]

        keyboard = [row_nsfw, row_spoiler]

        # Время
        if daily:
            time_buttons = []
            for t in TIMES:
                is_sel = time == t
                raw = t.replace(":", "")
                time_buttons.append(
                    InlineKeyboardButton(
                        f"{m.CHECK if is_sel else ''}{t}",
                        callback_data=f"{ST_PREFIX}time_{raw}",
                    )
                )
            for i in range(0, len(time_buttons), 3):
                keyboard.append(time_buttons[i : i + 3])

        return InlineKeyboardMarkup(keyboard)

    async def handle_settings(self, update: Update, context: CallbackContext):
        tg_user = await self.bot.get_or_create_tg_user(update)
        tu = await self._get_tarot_user(tg_user)

        time_str = tu.daily_time.strftime("%H:%M") if tu.daily_time else None
        text = self.messages.format_settings(tu.nsfw_allowed, tu.nsfw_spoiler, tu.daily_enabled, time_str)

        await update.message.reply_text(
            text,
            reply_markup=self._build_keyboard(tu.nsfw_allowed, tu.nsfw_spoiler, tu.daily_enabled, time_str),
            parse_mode=ParseMode.HTML,
        )

    async def handle_callback(self, update: Update, context: CallbackContext):
        query = update.callback_query
        await query.answer()

        action, value = query.data.removeprefix(ST_PREFIX).split("_", 1)

        if action == "close":
            await query.edit_message_text(self.messages.messages_CLOSED, reply_markup=None)
            return

        tg_user = await self.bot.get_or_create_tg_user(update)
        tu = await self._get_tarot_user(tg_user)

        if action == "nsfw":
            if value == "yes":
                tu.nsfw_allowed = True
            else:
                tu.nsfw_allowed = False

        elif action == "spoiler":
            if value == "yes":
                tu.nsfw_spoiler = True
            else:
                tu.nsfw_spoiler = False

        elif action == "daily":
            if value == "on":
                tu.daily_enabled = True
                if tu.daily_time is None:
                    middle_time = TIMES[len(TIMES) // 2]
                    hour, minute = map(int, middle_time.split(":"))
                    tu.daily_time = dt_time(hour, minute)
            else:
                tu.daily_enabled = False

        await tu.asave(update_fields=[
            "nsfw_allowed", "nsfw_spoiler",
            "daily_enabled", "daily_time", "updated_at"
        ])

        time_str = tu.daily_time.strftime("%H:%M") if tu.daily_time else None
        text = self.messages.format_settings(tu.nsfw_allowed, tu.nsfw_spoiler, tu.daily_enabled, time_str)

        await query.edit_message_text(
            text,
            reply_markup=self._build_keyboard(tu.nsfw_allowed, tu.nsfw_spoiler, tu.daily_enabled, time_str),
            parse_mode=ParseMode.HTML,
        )