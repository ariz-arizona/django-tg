import io
import os
import asyncio
import redis.asyncio as aioredis

from telegram import Update, InputMediaPhoto, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, MessageHandler, CallbackQueryHandler, CallbackContext, filters

from django.utils import timezone
from django.db.models import Count, Max

from tg_bot.bot.abstract import AbstractBot
from server.logger import logger

from tg_bot.models import TgUser, BotFile
from whirl.models import (
    WhirlUser,
    SirenRecord,
    SirenRecordImage,
    SirenRecordSound,
    SirenAttempt,
)

from .rendering import RenderingMixin
from .audio import AudioMixin
from .cooldown import CooldownService

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

SIREN_PAGE_SIZE = 4

MAX_VOICE_DURATION_SEC = 15
MAX_VOICE_FILE_SIZE_BYTES = 1 * 1024 * 1024

ANALYZE_TIMEOUT_SEC = 20
MAX_SIRENS_IN_MY = 20

def get_redis_client(
    db: int = 0, decode_responses: bool = True
) -> aioredis.StrictRedis:
    """Фабрика клиентов Redis."""
    return aioredis.StrictRedis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=db,
        decode_responses=decode_responses,
    )

redis_client = get_redis_client(db=3)

class WhirlBot(AudioMixin, RenderingMixin, AbstractBot):
    def __init__(self):
        self.handlers = self.get_handlers()
        self.cooldown = CooldownService(redis_client)

    def get_handlers(self):
        return [
            CommandHandler("start", self.handle_start, filters.ChatType.PRIVATE),
            CommandHandler("create", self.handle_create, filters.ChatType.PRIVATE),
            CommandHandler("my", self.handle_my, filters.ChatType.PRIVATE),
            CommandHandler("get", self.handle_get, filters.ChatType.PRIVATE),
            
            MessageHandler(
                filters.VOICE & filters.ChatType.PRIVATE,
                self.handle_voice_reply,
            ),
            
            CallbackQueryHandler(self.handle_siren_uact, pattern=r"^siren_uact_"),
            CallbackQueryHandler(self.handle_siren_page, pattern=r"^siren_page:\d+$"),
            CallbackQueryHandler(self.handle_siren_pick, pattern=r"^siren_pick:.+$"),
            CallbackQueryHandler(self.handle_siren_noop, pattern=r"^siren_noop$"),
        ]
        
    @staticmethod
    def _score_verdict(score: float) -> str:
        """Словесная оценка совпадения — чтобы не только цифра, но и эмоция."""
        if score >= 90:
            return "Почти идеальная сирена 🚨"
        if score >= 65:
            return "Очень похоже!"
        if score >= 40:
            return "Сирена немного заблудилась"
        return "Это был скорее грустный чайник"
    
    @staticmethod
    def _result_keyboard(record_id: int) -> InlineKeyboardMarkup:
        """Клавиатура после разбора: действия привязаны к конкретной сирене."""
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔁 Повторить",
                    callback_data=f"siren_uact_pick:{record_id}",
                ),
            ],
            [
                InlineKeyboardButton("🔊 Все сирены", callback_data="siren_uact_get"),
                InlineKeyboardButton("📊 Мой профиль", callback_data="siren_uact_my"),
            ],
        ])
        
    async def get_or_create_virtual_user(self, update: Update) -> WhirlUser:
        """Находит или создаёт TgUser по данным Telegram."""
        tg_user = update.effective_user
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
        # 2. Находим или создаем профиль для этого пользователя
        whirl_user, _ = await WhirlUser.objects.aget_or_create(
            user=user,
            defaults={
                "is_admin": False,
            },
        )

        user.whirl_user = whirl_user
        return whirl_user

    async def handle_siren_uact(self, update: Update, context: CallbackContext) -> None:
        """
        Роутер для кнопок подвала «Мой профиль» / «Все сирены» / «Повторить».
        Гасит часики, снимает клавиатуру с исходного сообщения (защита от
        повторного клика) и делегирует в соответствующий handler.
        """
        query = update.callback_query
        await query.answer()

        # Снимаем клавиатуру с сообщения, на котором была нажата кнопка.
        # Иначе юзер может нажать ещё раз, пока идёт обработка, и получить
        # два одинаковых ответа.
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception as e:
            logger.warning(f"Не удалось снять клавиатуру у {query.message.message_id}: {e}")

        action = query.data[len("siren_uact_"):]

        if action == "my":
            await self.handle_my(update, context)
        elif action == "get":
            await self.handle_get(update, context)
        elif action.startswith("pick:"):
            record_id = int(action.split(":", 1)[1])
            await self.cooldown.reset(
                update.get_bot(),
                update.effective_chat.id,
                update.effective_user.id,
            )
            await self._dispatch_siren_pick(update, context, record_id)
        else:
            logger.warning(f"Неизвестный siren_uact: {query.data!r}")
            
    async def handle_start(self, update: Update, context: CallbackContext) -> None:
        user = await self.get_or_create_virtual_user(update)

        text = (
            "👋 Привет! Это бот «Повтори сирену».\n\n"
            "Я присылаю паттерн сирены — картинку и звук, ты пытаешься "
            "повторить его голосовым сообщением, а я говорю, насколько "
            "точно получилось.\n\n"
            "🔊 Получить паттерн: /get <slug>"
        )
        if user and user.is_admin:
            text += "\n🛠 Ты админ, тебе доступна /create <slug> <название>"

        await update.effective_message.reply_text(text)

    async def handle_create(self, update: Update, context: CallbackContext) -> None:
        """
        /create <slug> <title...>
        Доступно только VirtualUser с is_admin=True.
        Создаёт SirenRecord в is_active=False, загружает картинку и звук,
        и только при успехе переключает в is_active=True. Если на любом
        шаге после acreate упало — запись остаётся неактивной.
        """
        user = await self.get_or_create_virtual_user(update)

        if not user or not user.is_admin:
            await update.effective_message.reply_text(
                "⛔ Команда доступна только администраторам."
            )
            return

        args = context.args
        if not args:
            await update.effective_message.reply_text(
                "Использование: /create <slug> <название сирены> [--pattern 0,1,2,1,2,0,0]"
            )
            return

        pattern = None
        if "--pattern" in args:
            idx = args.index("--pattern")
            try:
                pattern = [float(x) for x in args[idx + 1].split(",")]
            except (IndexError, ValueError):
                await update.effective_message.reply_text(
                    "❌ Некорректный формат --pattern, ожидается список чисел через запятую."
                )
                return
            args = args[:idx]

        slug = args[0]
        title = " ".join(args[1:]) or slug

        if await SirenRecord.objects.filter(slug=slug).aexists():
            await update.effective_message.reply_text(
                f"❌ Запись с slug «{slug}» уже существует."
            )
            return

        try:
            sequence, normalized_curve = self.generate_pattern(pattern=pattern)
        except Exception as e:
            logger.error(f"Ошибка генерации паттерна сирены: {e}", exc_info=True)
            await update.effective_message.reply_text(
                "❌ Не удалось сгенерировать паттерн."
            )
            return

        image_bytes = self.render_pattern_image(normalized_curve)
        sound_bytes = self.render_pattern_sound(sequence)

        # Создаём сразу неактивной — включим только после успешной загрузки
        # картинки и звука. Если что-то упадёт ниже, запись останется
        # is_active=False и в игру не попадёт.
        record = await SirenRecord.objects.acreate(
            slug=slug,
            title=title,
            created_by=user,
            generated_sequence=sequence,
            normalized_curve=normalized_curve,
            is_active=False,
        )

        try:
            # Картинка
            image_buf = io.BytesIO(image_bytes)
            image_buf.name = f"{slug}.png"
            sent_image = await update.effective_message.reply_photo(
                photo=image_buf,
                read_timeout=30,
                write_timeout=30,
                connect_timeout=10,
            )
            image_asset = await SirenRecordImage.objects.acreate(record=record)
            await BotFile.objects.acreate(
                content_object=image_asset,
                bot_id=self.app_bot_id,
                file_id=sent_image.photo[-1].file_id,
            )

            # Звук: WAV → OGG/Opus, чтобы Telegram принял как voice
            ogg_bytes = self._wav_to_ogg_opus(sound_bytes)
            sound_buf = io.BytesIO(ogg_bytes)
            sound_buf.name = f"{slug}.ogg"
            sent_sound = await update.effective_message.reply_voice(
                voice=sound_buf,
                read_timeout=30,
                write_timeout=30,
                connect_timeout=10,
            )
            sound_asset = await SirenRecordSound.objects.acreate(record=record)
            await BotFile.objects.acreate(
                content_object=sound_asset,
                bot_id=self.app_bot_id,
                file_id=sent_sound.voice.file_id,
            )
        except Exception as e:
            logger.error(
                f"Не удалось загрузить файлы для сирены {record.pk} "
                f"({record.slug}): {e}",
                exc_info=True,
            )
            await update.effective_message.reply_text(
                f"❌ Не удалось загрузить файлы. Сирена «{record.title}» "
                f"сохранена неактивной — включи её вручную после исправления."
            )
            return

        # Всё загрузилось — включаем
        await SirenRecord.objects.filter(pk=record.pk).aupdate(is_active=True)
        record.is_active = True

        logger.info(f"Создана новая запись сирены: {record}")
        await update.effective_message.reply_text(
            f"✅ Сирена «{record.title}» сохранена под slug «{record.slug}»."
        )

    async def handle_my(self, update: Update, context: CallbackContext) -> None:
        """Личный кабинет: данные юзера и все сирены, по которым он играл."""
        query = update.callback_query
        if query is not None:
            await query.answer()

        user = await self.get_or_create_virtual_user(update)

        tg = update.effective_user
        name = tg.first_name or tg.username or f"id{tg.id}"

        # Один запрос: по каждой сирене — число попыток и лучший результат.
        # Сортировка по лучшему результату (сверху — самое успешное).
        rows = (
            SirenAttempt.objects
            .filter(user=user, status=SirenAttempt.Status.SUCCESS)
            .values("record__id", "record__title")
            .annotate(
                attempts=Count("id"),
                best_score=Max("score"),
            )
            .order_by("-best_score")
        )
        rows = [r async for r in rows]

        lines = [
            f"👤 {name}",
            f"🆔 tg_id: {tg.id}",
            "",
        ]

        if not rows:
            lines.append("Пока ни одной завершённой попытки.")
            lines.append("Начни с /get — выбери сирену и запиши голосовое.")
        else:
            shown = rows[:MAX_SIRENS_IN_MY]
            hidden = len(rows) - len(shown)

            if hidden:
                lines.append(f"🎮 Показаны первые {MAX_SIRENS_IN_MY} из {len(rows)}:")
            else:
                lines.append(f"🎮 Сирен в игре: {len(rows)}")
            lines.append("")

            for r in shown:
                verdict = self._score_verdict(r["best_score"])
                lines.append(
                    f"🔊 «{r['record__title']}»\n"
                    f"   попыток: {r['attempts']}, лучший: {r['best_score']}% — {verdict}"
                )

            if hidden:
                lines.append("")
                lines.append(f"… и ещё {hidden}")

        text = "\n".join(lines)
        await update.effective_message.reply_text(text)
        
    async def build_siren_list(self, page: int) -> tuple[str, InlineKeyboardMarkup]:
        """
        Текст + клавиатура для страницы списка сирен: кнопки с сквозными
        номерами записей на этой странице (по 2 в ряд) + навигация внизу.
        """
        total = await SirenRecord.objects.filter(is_active=True).acount()
        offset = page * SIREN_PAGE_SIZE

        def truncate(text: str, limit: int = 64) -> str:
            """Обрезает текст под лимит кнопки Telegram (≈64 символа)."""
            return text if len(text) <= limit else text[: limit - 1] + "…"

        records = [
            r async for r in
            SirenRecord.objects.filter(is_active=True)
            .order_by("id")[offset:offset + SIREN_PAGE_SIZE]
        ]

        buttons = []
        row = []
        for record in records:
            row.append(
                InlineKeyboardButton(
                    truncate(record.title),
                    callback_data=f"siren_pick:{record.id}",
                )
            )
            if len(row) == 2:  # было 4
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        has_prev = page > 0
        has_next = offset + SIREN_PAGE_SIZE < total

        if has_next or has_prev:
            buttons.append([
                InlineKeyboardButton(
                    "⬅️ Назад" if has_prev else "📢",
                    callback_data=f"siren_page:{page - 1}" if has_prev else "siren_noop",
                ),
                InlineKeyboardButton(
                    "Вперёд ➡️" if has_next else "📢",
                    callback_data=f"siren_page:{page + 1}" if has_next else "siren_noop",
                ),
            ])

        # Склонение слова "сирена"
        if total % 10 == 1 and total % 100 != 11:
            word = "сирена"
        elif total % 10 in (2, 3, 4) and total % 100 not in (12, 13, 14):
            word = "сирены"
        else:
            word = "сирен"

        text = f"У меня есть {total} {word}. Выбери одну:"
        return text, InlineKeyboardMarkup(buttons)

    async def send_siren_record(self, message, user: WhirlUser, record: SirenRecord) -> None:
        """
        Общая логика: шлёт картинку с подписью-инструкцией + voice эталона,
        отменяет прежние WAITING попытки пользователя и создаёт новую.
        Используется и из /get <slug>, и из клика по номеру в инлайн-списке —
        message может быть как Update.effective_message, так и
        CallbackQuery.message.
        """
        try:
            image_asset = await SirenRecordImage.objects.aget(record=record)
            sound_asset = await SirenRecordSound.objects.aget(record=record)
        except (SirenRecordImage.DoesNotExist, SirenRecordSound.DoesNotExist):
            await message.reply_text(f"❌ Для сирены «{record.title}» не найдены файлы.")  # было slug
            return

        image_file_id = await image_asset.aget_file_id(self.app_bot_id, default=None)
        sound_file_id = await sound_asset.aget_file_id(self.app_bot_id, default=None)

        if not image_file_id or not sound_file_id:
            await message.reply_text(
                f"❌ Для сирены «{record.title}» не найдены файлы этого бота."
            ) 
            return
        # Фото с подписью — сразу и название, и инструкция. Это экономит
        # отдельное сообщение с текстом.
        await message.reply_photo(
            photo=image_file_id,
            caption=(
                f"🔊 «{record.title}»\n\n"
                "🎤 Запиши голосовое — попробуй повторить этот паттерн."
            ),
        )
        await message.reply_voice(voice=sound_file_id)

        await SirenAttempt.objects.filter(
            user=user, status=SirenAttempt.Status.WAITING
        ).aupdate(status=SirenAttempt.Status.CANCELLED)

        await SirenAttempt.objects.acreate(
            user=user,
            record=record,
            status=SirenAttempt.Status.WAITING,
        )

        if getattr(message, "reply_markup", None) is not None:
            try:
                await message.edit_text(
                    f"Вы вызвали сирену {record.title}",
                    reply_markup=None,
                )
            except Exception as e:
                logger.warning(
                    f"Не удалось отредактировать сообщение списка сирен: {e}"
                )

    async def handle_get(self, update: Update, context: CallbackContext) -> None:
        """
        /get <slug> — отправляет конкретную запись.
        /get без slug — присылает клавиатуру сирен с пагинацией.
        Колбек siren_uact_get (из подвала результата) — то же, что /get без аргумента.
        """
        query = update.callback_query            
        message = update.effective_message
        
        args = context.args or []
        
        if query is not None or not args:
            text, keyboard = await self.build_siren_list(page=0)
            await message.reply_text(text, reply_markup=keyboard)
            return
        
        if not await self.cooldown.use(update):
            return 
        
        try:
            record = await SirenRecord.objects.aget(slug=args[0], is_active=True)
        except SirenRecord.DoesNotExist:
            await update.effective_message.reply_text(
                f"❌ Сирена «{args[0]}» не найдена."
            )
            return

        user = await self.get_or_create_virtual_user(update)
        await self.send_siren_record(update.effective_message, user, record)

    async def handle_siren_page(self, update: Update, context: CallbackContext) -> None:
        """Переключение страницы списка — просто перерисовывает клавиатуру на месте."""
        query = update.callback_query
        page = int(query.data.split(":")[1])
        text, keyboard = await self.build_siren_list(page=page)
        await query.edit_message_text(text, reply_markup=keyboard)
        await query.answer()

    async def handle_siren_pick(self, update: Update, context: CallbackContext) -> None:
        """Клик по записи в списке — эквивалент /get <slug>."""
        query = update.callback_query
        await query.answer()

        record_id = int(query.data.split(":", 1)[1])
        await self._dispatch_siren_pick(update, context, record_id)
        
    async def _dispatch_siren_pick(
        self, update: Update, context: CallbackContext, record_id: int
    ) -> None:
        """Общая логика «показать конкретную сирену по id» — для siren_pick и siren_uact_pick."""
        if not await self.cooldown.use(update):
            return

        try:
            record = await SirenRecord.objects.aget(id=record_id, is_active=True)
        except SirenRecord.DoesNotExist:
            await update.effective_message.reply_text(
                f"❌ Сирена #{record_id} не найдена."
            )
            return

        user = await self.get_or_create_virtual_user(update)
        await self.send_siren_record(update.effective_message, user, record)

    async def handle_siren_noop(self, update: Update, context: CallbackContext) -> None:
        """Клик по неактивной кнопке навигации — просто гасим "часики" на кнопке."""
        await update.callback_query.answer()
        
    # ---------- handle_voice_reply: оркестратор ----------

    async def handle_voice_reply(self, update: Update, context: CallbackContext) -> None:
        """
        Ловит голосовое пользователя и, если есть активная WAITING-попытка,
        разбирает его как попытку повторить сирену.

        Логика разбита на маленькие шаги: лимиты → захват попытки →
        приглашение → скачивание → анализ → результат. Каждый шаг знает
        только про себя, оркестрация — здесь.
        """
        user = await self.get_or_create_virtual_user(update)
        voice = update.effective_message.voice
        if voice is None:
            return

        # 1. Лимиты
        if not await self._check_voice_limits(update, voice):
            return

        # 2. Активная попытка
        attempt = (
            await SirenAttempt.objects.select_related("record")
            .filter(user=user, status=SirenAttempt.Status.WAITING)
            .order_by("-created_at")
            .afirst()
        )
        if attempt is None:
            await update.effective_message.reply_text(
                "Сначала выбери сирену, которую хочешь повторить! 🔊\n"
                "Отправь /get или выбери из списка."
            )
            return

        # 3. Атомарный захват
        if not await self._claim_attempt(attempt, user):
            return

        record = attempt.record

        # 4. Приглашение «Разбираю…»
        try:
            invite_msg = await self._send_invite_message(update, record)            
            attempt.reply_message_id = invite_msg.message_id
            await attempt.asave(update_fields=["reply_message_id", "updated_at"])
        except Exception as e:
            logger.error(f"Не удалось отправить приглашение: {e}", exc_info=True)
            await self._fail_attempt(
                attempt, update, None,  # invite_msg ещё нет
                "❌ Не удалось подготовить разбор. Попробуй ещё раз.",
            )
            return

        # 5. Скачивание
        try:
            voice_bytes = await self._download_voice(context, voice.file_id)
        except Exception as e:
            logger.error(
                f"Не удалось скачать голосовое {voice.file_id}: {e}",
                exc_info=True,
            )
            await self._fail_attempt(
                attempt, update, invite_msg,
                "❌ Не удалось скачать голосовое. Попробуй ещё раз.",
            )
            return

        # 6. Анализ (CPU в потоке, с таймаутом)
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(self._analyze_voice, voice_bytes, record),
                timeout=ANALYZE_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            logger.error(
                f"Анализ голосового не уложился в {ANALYZE_TIMEOUT_SEC} сек — "
                f"поток продолжает работу, но результат будет отброшен."
            )
            await self._fail_attempt(
                attempt, update, invite_msg,
                "⏱ Не успел разобрать голосовое. Попробуй ещё раз.",
            )
            return
        except Exception as e:
            logger.error(f"Ошибка разбора голосового: {e}", exc_info=True)
            await self._fail_attempt(
                attempt, update, invite_msg,
                "❌ Не удалось разобрать голосовое сообщение.",
            )
            return

        # 7. Успех
        await self._save_success(attempt, result)
        await self._render_result(update, invite_msg, attempt, record, result)

    # ---------- шаги ----------

    async def _check_voice_limits(self, update: Update, voice) -> bool:
        """True — лимиты ок, False — уже ответили юзеру и надо выйти."""
        if voice.duration and voice.duration > MAX_VOICE_DURATION_SEC:
            await update.effective_message.reply_text(
                f"⏱ Слишком длинное голосовое — максимум {MAX_VOICE_DURATION_SEC} сек."
            )
            return False
        if voice.file_size and voice.file_size > MAX_VOICE_FILE_SIZE_BYTES:
            await update.effective_message.reply_text(
                "📦 Файл слишком большой."
            )
            return False
        return True

    async def _claim_attempt(self, attempt: SirenAttempt, user: WhirlUser) -> bool:
        """Атомарно WAITING → PROCESSING. False, если попытку уже захватили."""
        updated = await SirenAttempt.objects.filter(
            id=attempt.id, status=SirenAttempt.Status.WAITING
        ).aupdate(status=SirenAttempt.Status.PROCESSING)
        if updated == 0:
            logger.info(
                f"Игнор второго войса от {user} (попытка {attempt.id} уже PROCESSING)"
            )
            return False
        return True

    async def _send_invite_message(self, update: Update, record: SirenRecord):
        image_asset = await SirenRecordImage.objects.aget(record=record)
        reference_file_id = await image_asset.aget_file_id(self.app_bot_id, default=None)
        if not reference_file_id:
            raise RuntimeError(f"Нет картинки-эталона для сирены {record.id}")
        return await update.effective_message.reply_photo(
            photo=reference_file_id,
            caption="⏳ Разбираю твоё голосовое, готовлю результат…",
        )

    async def _download_voice(self, context: CallbackContext, file_id: str) -> bytes:
        """Скачивает голосовое в память и возвращает bytes."""
        tg_file = await context.bot.get_file(
            file_id,
            connect_timeout=10,
            read_timeout=30,
            pool_timeout=10,
        )
        buf = io.BytesIO()
        await tg_file.download_to_memory(buf)
        return buf.getvalue()

    def _analyze_voice(self, voice_bytes: bytes, record: SirenRecord) -> dict:
        """
        Синхронная CPU-цепочка: кривая → сглаживание → выравнивание →
        score → картинка сравнения. Запускается через asyncio.to_thread,
        поэтому внутри не должно быть await.
        """
        raw_curve = self.extract_normalized_curve(voice_bytes)
        smoothed = self.smooth_curve(raw_curve)
        thresholded = self.apply_threshold(smoothed)
        aligned = self.align_user_curve(record.normalized_curve, thresholded)
        score = self.compute_match_score(record.normalized_curve, aligned)
        image_bytes = self.render_comparison_image(record.normalized_curve, aligned)
        return {"aligned": aligned, "score": score, "image_bytes": image_bytes}

    async def _save_success(self, attempt: SirenAttempt, result: dict) -> None:
        """Помечает попытку SUCCESS и сохраняет score и кривую."""
        await SirenAttempt.objects.filter(pk=attempt.pk).aupdate(
            status=SirenAttempt.Status.SUCCESS,
            score=result["score"],
            user_curve=result["aligned"],
            updated_at=timezone.now(),
        )

    async def _fail_attempt(
        self,
        attempt: SirenAttempt,
        update: Update,
        invite_msg,
        text: str,
    ) -> None:
        """
        Откат PROCESSING → CANCELLED + сообщение юзеру.

        Если invite_msg есть — редактируем его caption. Если нет
        (приглашение не успело создаться) — шлём новое сообщение и
        записываем его id в attempt.reply_message_id, чтобы потом
        (например, при отмене через /get) можно было его найти.
        """
        await SirenAttempt.objects.filter(
            pk=attempt.pk, status=SirenAttempt.Status.PROCESSING
        ).aupdate(
            status=SirenAttempt.Status.CANCELLED,
            updated_at=timezone.now(),
        )

        if invite_msg is not None:
            try:
                await update.get_bot().edit_message_caption(
                    chat_id=update.effective_chat.id,
                    message_id=invite_msg.message_id,
                    caption=text,
                )
                return
            except Exception as e:
                logger.warning(
                    f"Не удалось отредактировать invite_msg "
                    f"{invite_msg.message_id}: {e}"
                )
                # падаем в fallback — шлём новым сообщением

        msg = await update.effective_message.reply_text(text)
        await SirenAttempt.objects.filter(pk=attempt.pk).aupdate(
            reply_message_id=msg.message_id,
            updated_at=timezone.now(),
        )

    async def _render_result(
        self,
        update: Update,
        invite_msg,
        attempt: SirenAttempt,
        record: SirenRecord,
        result: dict,
    ) -> None:
        """Подменяет приглашение на картинку с результатом и клавиатуру."""
        image_buf = io.BytesIO(result["image_bytes"])
        image_buf.name = f"{record.slug}_attempt.png"

        verdict = self._score_verdict(result["score"])
        caption = (
            f"{result['score']}% — {verdict}\n\n"
            f"Твоя попытка повторить «{record.title}»"
        )

        try:
            await update.get_bot().edit_message_media(
                chat_id=update.effective_chat.id,
                message_id=invite_msg.message_id,
                media=InputMediaPhoto(media=image_buf, caption=caption),
                reply_markup=self._result_keyboard(record.id),
                read_timeout=30,
                write_timeout=30,
                connect_timeout=10,
            )
        except Exception as e:
            logger.error(f"Не удалось отредактировать сообщение: {e}", exc_info=True)
            image_buf.seek(0)
            await update.effective_message.reply_photo(
                photo=image_buf,
                caption=caption,
                reply_markup=self._result_keyboard(record.id),
            )
