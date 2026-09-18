import os
import logging
import redis.asyncio as aioredis

from telegram import Update

from server.logger import logger

SIREN_COOLDOWN_SECONDS = 10
SIREN_COOLDOWN_KEY_TEMPLATE = "siren:cd:{chat_id}:{user_id}"
SIREN_COOLDOWN_MSG_KEY_TEMPLATE = "siren_cooldown_message:{chat_id}:{user_id}"

class CooldownService:
    """
    Кулдаун per-chat-per-user с сообщением-предупреждением.

    Ключи в Redis:
      siren:cd:{chat_id}:{user_id}            — таймер, значение message_id или 0, TTL = окно
      siren_cooldown_message:{chat_id}:{user_id} — id последнего сообщения-предупреждения
    """

    MSG_KEY_TEMPLATE = "siren_cooldown_message:{chat_id}:{user_id}"

    def __init__(
        self,
        redis,
        cooldown_seconds: int = SIREN_COOLDOWN_SECONDS,
        key_template: str = SIREN_COOLDOWN_KEY_TEMPLATE,
        logger_: logging.Logger | None = None,
    ) -> None:
        self._redis = redis
        self._cooldown_seconds = cooldown_seconds
        self._key_template = key_template
        self._log = logger_ or logger

    # ---------- служебное ----------

    def _timer_key(self, chat_id: int, user_id: int) -> str:
        return self._key_template.format(chat_id=chat_id, user_id=user_id)

    def _msg_key(self, chat_id: int, user_id: int) -> str:
        return self.MSG_KEY_TEMPLATE.format(chat_id=chat_id, user_id=user_id)

    # ---------- таймер кулдауна ----------

    async def set(
        self,
        chat_id: int,
        user_id: int,
        message_id: int = 0,
        seconds: int | None = None,
    ) -> None:
        """
        Ставит/обновляет таймер кулдауна.
        message_id = 0 — сообщение-предупреждение ещё не отправлено.
        """
        ttl = self._cooldown_seconds if seconds is None else seconds
        await self._redis.set(
            self._timer_key(chat_id, user_id), message_id, ex=ttl,
        )

    async def check(
        self, chat_id: int, user_id: int
    ) -> tuple[int, int] | None:
        """
        None — кулдауна нет, можно действовать.
        (elapsed, message_id) — сколько секунд уже прошло и id сообщения
        (0, если сообщение ещё не отправлялось).
        """
        key = self._timer_key(chat_id, user_id)

        ttl = await self._redis.ttl(key)
        if ttl is None or ttl < 0:
            return None

        raw = await self._redis.get(key)
        try:
            message_id = int(raw) if raw is not None else 0
        except (TypeError, ValueError):
            message_id = 0

        elapsed = max(self._cooldown_seconds - ttl, 0)
        return elapsed, message_id

    # ---------- стор сообщения-предупреждения ----------

    async def set_message(
        self, chat_id: int, user_id: int, message_id: int
    ) -> None:
        await self._redis.set(self._msg_key(chat_id, user_id), message_id)

    async def get_message(
        self, chat_id: int, user_id: int
    ) -> int | None:
        raw = await self._redis.get(self._msg_key(chat_id, user_id))
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    async def clear_message(self, chat_id: int, user_id: int) -> None:
        await self._redis.delete(self._msg_key(chat_id, user_id))

    # ---------- операции с сообщением в Telegram ----------

    async def _delete_previous_message(
        self, bot, chat_id: int, user_id: int
    ) -> None:
        prev_id = await self.get_message(chat_id, user_id)
        if prev_id is None:
            return
        try:
            await bot.delete_message(chat_id=chat_id, message_id=prev_id)
        except Exception as e:
            self._log.warning(
                f"Не удалось удалить прошлое кулдаун-сообщение: {e}"
            )
        finally:
            await self.clear_message(chat_id, user_id)

    async def _edit_message(
        self, bot, chat_id: int, message_id: int, remaining: int
    ) -> bool:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=f"⏳ Подожди ещё {remaining} сек.",
            )
            return True
        except Exception as e:
            self._log.warning(
                f"Не удалось отредактировать кулдаун-сообщение: {e}"
            )
            return False

    # ---------- основной вход ----------

    async def use(self, update: Update) -> bool:
        """
        True  — кулдауна не было, действие разрешено (таймер поставлен).
        False — кулдаун активен, юзеру показано/обновлено предупреждение.
        """
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        bot = update.get_bot()

        cooldown = await self.check(chat_id, user_id)
        self._log.info(cooldown)

        # Кулдауна нет — можно действовать.
        if cooldown is None:
            await self._delete_previous_message(bot, chat_id, user_id)
            await self.set(chat_id, user_id, message_id=0)
            return True

        elapsed, message_id = cooldown
        remaining = max(self._cooldown_seconds - elapsed, 0)

        # Сообщения ещё нет — отправляем и привязываем к обоим ключам.
        if message_id == 0:
            msg = await update.effective_message.reply_text(
                f"⏳ Подожди ещё {remaining} сек."
            )
            await self.set(
                chat_id, user_id, message_id=msg.message_id, seconds=remaining,
            )
            await self.set_message(chat_id, user_id, msg.message_id)
            return False

        # Сообщение есть — пробуем отредактировать.
        edited = await self._edit_message(
            bot, chat_id, message_id, remaining,
        )
        if not edited:
            msg = await update.effective_message.reply_text(
                f"⏳ Подожди ещё {remaining} сек."
            )
            await self.set(
                chat_id, user_id, message_id=msg.message_id, seconds=remaining,
            )
            await self.set_message(chat_id, user_id, msg.message_id)
        return False