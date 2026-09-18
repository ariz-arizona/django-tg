import io
import os
import asyncio
import redis.asyncio as aioredis

from telegram import Update, InputMediaPhoto, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CommandHandler, MessageHandler, CallbackQueryHandler, CallbackContext, filters

from django.utils import timezone

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
            CommandHandler("get", self.handle_get, filters.ChatType.PRIVATE),
            MessageHandler(
                filters.VOICE & filters.ChatType.PRIVATE,
                self.handle_voice_reply,
            ),
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
        Генерирует паттерн + картинку + звук эталона и сохраняет SirenRecord.
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
            args = args[:idx]  # убираем флаг и значение из аргументов title

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

        record = await SirenRecord.objects.acreate(
            slug=slug,
            title=title,
            created_by=user,
            generated_sequence=sequence,
            normalized_curve=normalized_curve,
        )

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

        logger.info(f"Создана новая запись сирены: {record}")
        await update.effective_message.reply_text(
            f"✅ Сирена «{record.title}» сохранена под slug «{record.slug}»."
        )

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
        """
        user = await self.get_or_create_virtual_user(update)

        args = context.args
        if args:
            if not await self.cooldown.use(update):
                return 
            
        if not args:
            text, keyboard = await self.build_siren_list(page=0)
            await update.effective_message.reply_text(text, reply_markup=keyboard)
            return
        
        try:
            record = await SirenRecord.objects.aget(slug=args[0], is_active=True)
        except SirenRecord.DoesNotExist:
            await update.effective_message.reply_text(
                f"❌ Сирена «{args[0]}» не найдена."
            )
            return

        await self.send_siren_record(update.effective_message, user, record)

    async def handle_siren_page(self, update: Update, context: CallbackContext) -> None:
        """Переключение страницы списка — просто перерисовывает клавиатуру на месте."""
        query = update.callback_query
        page = int(query.data.split(":")[1])
        text, keyboard = await self.build_siren_list(page=page)
        await query.edit_message_text(text, reply_markup=keyboard)
        await query.answer()

    async def handle_siren_pick(self, update: Update, context: CallbackContext) -> None:
        """Клик по номеру записи в списке — эквивалент /get <slug>."""
        query = update.callback_query
        await query.answer()
        
        if not await self.cooldown.use(update):
            return
        
        record_id = int(query.data.split(":", 1)[1])
        try:
            record = await SirenRecord.objects.aget(id=record_id, is_active=True)
        except SirenRecord.DoesNotExist:
            await query.message.reply_text(
                f"❌ Сирена #{record_id} не найдена."
            )
            return

        user = await self.get_or_create_virtual_user(update)
        await self.send_siren_record(query.message, user, record)

    async def handle_siren_noop(self, update: Update, context: CallbackContext) -> None:
        """Клик по неактивной кнопке навигации — просто гасим "часики" на кнопке."""
        await update.callback_query.answer()
        
    async def handle_voice_reply(self, update: Update, context: CallbackContext) -> None:
        """
        Ловит голосовое сообщение пользователя. Если перед этим он
        запрашивал сирену через /get — считает это попыткой её повторить,
        строит normalized_curve из записи и рисует её тем же render_pattern_image,
        что и эталон.

        Вся CPU-тяжёлая обработка звука и рендер картинки вынесены в
        asyncio.to_thread(), чтобы не блокировать event loop бота.
        """
        user = await self.get_or_create_virtual_user(update)
        voice = update.effective_message.voice
        if voice is None:
            return
        
        # Отсекаем слишком длинные/тяжёлые голосовые до любой обработки:
        # дорогая цепочка extract_* / render_* на длинной записи съест
        # CPU и время впустую.
        if voice.duration and voice.duration > MAX_VOICE_DURATION_SEC:
            await update.effective_message.reply_text(
                f"⏱ Слишком длинное голосовое — максимум "
                f"{MAX_VOICE_DURATION_SEC} секунд. Запиши покороче."
            )
            return

        if voice.file_size and voice.file_size > MAX_VOICE_FILE_SIZE_BYTES:
            await update.effective_message.reply_text(
                f"📦 Файл слишком большой — максимум "
                f"{MAX_VOICE_FILE_SIZE_BYTES // (1024 * 1024)} МБ."
            )
            return

        attempt = (
            await SirenAttempt.objects.select_related("record")
            .filter(user=user, status=SirenAttempt.Status.WAITING)
            .order_by("-created_at")
            .afirst()
        )

        if attempt is None:
            return

        record = attempt.record

        # 1) Отправляем эталонную картинку с подписью "разбираю"
        image_asset = await SirenRecordImage.objects.aget(record=record)
        reference_file_id = await image_asset.aget_file_id(self.app_bot_id, default=None)

        if reference_file_id:
            invite_msg = await update.effective_message.reply_photo(
                photo=reference_file_id,
                caption="⏳ Разбираю твоё голосовое, готовлю результат…",
            )
        else:
            invite_msg = await update.effective_message.reply_text(
                "⏳ Разбираю твоё голосовое, готовлю результат…"
            )

        attempt.reply_message_id = invite_msg.message_id
        await attempt.asave(update_fields=["reply_message_id", "updated_at"])

        # 2) Скачиваем голосовое (I/O — оставляем в loop)
        tg_file = await context.bot.get_file(voice.file_id)
        buf = io.BytesIO()
        await tg_file.download_to_memory(buf)
        voice_bytes = buf.getvalue()

        # 3) CPU-тяжёлая обработка — в отдельный поток
        def _analyze() -> dict:
            raw_curve = self.extract_normalized_curve(voice_bytes)
            smoothed = self.smooth_curve(raw_curve)
            thresholded = self.apply_threshold(smoothed)
            aligned = self.align_user_curve(record.normalized_curve, thresholded)
            score = self.compute_match_score(record.normalized_curve, aligned)
            image_bytes = self.render_comparison_image(
                record.normalized_curve, aligned
            )
            return {
                "aligned": aligned,
                "score": score,
                "image_bytes": image_bytes,
            }

        try:
            result = await asyncio.to_thread(_analyze)
        except Exception as e:
            logger.error(f"Ошибка разбора голосового: {e}", exc_info=True)
            try:
                await context.bot.edit_message_caption(
                    chat_id=update.effective_chat.id,
                    message_id=attempt.reply_message_id,
                    caption="❌ Не удалось разобрать голосовое сообщение.",
                )
            except Exception:
                await update.effective_message.reply_text(
                    "❌ Не удалось разобрать голосовое сообщение."
                )
            return

        await SirenAttempt.objects.filter(pk=attempt.pk).aupdate(
            status=SirenAttempt.Status.SUCCESS,
            score=result["score"],
            user_curve=result["aligned"],
            updated_at=timezone.now(),
        )

        # 4) Редактируем то же сообщение — подменяем картинку на результат
        image_buf = io.BytesIO(result["image_bytes"])
        image_buf.name = f"{record.slug}_attempt.png"

        verdict = self._score_verdict(result["score"])
        caption = (
            f"{result['score']}% — {verdict}\n\n"
            f"Твоя попытка повторить «{record.title}»"
        )

        try:
            await context.bot.edit_message_media(
                chat_id=update.effective_chat.id,
                message_id=attempt.reply_message_id,
                media=InputMediaPhoto(media=image_buf, caption=caption),
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
            )