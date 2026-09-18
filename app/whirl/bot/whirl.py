import io
import os
import wave

import numpy as np
from PIL import Image, ImageDraw
from pydub import AudioSegment
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

from .cooldown import CooldownService

PEAK_THRESHOLD = 0.15  # можно вынести в константу класса

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

SIREN_PAGE_SIZE = 8

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

class WhirlBot(AbstractBot):
    def __init__(self):
        self.handlers = self.get_handlers()
        self.cooldown = CooldownService(redis_client)

    def get_handlers(self):
        return [
            CommandHandler("start", self.handle_start, filters.ChatType.PRIVATE),
            CommandHandler("create", self.handle_create, filters.ChatType.PRIVATE),
            CommandHandler("get", self.handle_get, filters.ChatType.PRIVATE),
            MessageHandler(filters.VOICE, self.handle_voice_reply),
            CallbackQueryHandler(self.handle_siren_page, pattern=r"^siren_page:\d+$"),
            CallbackQueryHandler(self.handle_siren_pick, pattern=r"^siren_pick:.+$"),
            CallbackQueryHandler(self.handle_siren_noop, pattern=r"^siren_noop$"),
        ]

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
            "👋 Привет! Это бот «Угадай сирену».\n\n"
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
        sent_image = await update.effective_message.reply_photo(photo=image_buf)
        image_asset = await SirenRecordImage.objects.acreate(record=record)
        await BotFile.objects.acreate(
            content_object=image_asset,
            bot_id=self.app_bot_id,
            file_id=sent_image.photo[-1].file_id,
        )

        # render_pattern_sound отдаёт WAV, а не ogg/opus, поэтому шлём
        # его как аудио-файл, а не как voice-заметку — Telegram сам
        # решит, как это проигрывать, но полноценным voice-message
        # (с волной-иконкой) это не станет. Если нужен именно voice —
        # WAV придётся перекодировать в ogg/opus (например, через
        # pydub + ffmpeg) перед отправкой.
        sound_buf = io.BytesIO(sound_bytes)
        sound_buf.name = f"{slug}.wav"
        sent_sound = await update.effective_message.reply_audio(
            audio=sound_buf, title=title
        )
        sound_asset = await SirenRecordSound.objects.acreate(record=record)
        await BotFile.objects.acreate(
            content_object=sound_asset,
            bot_id=self.app_bot_id,
            file_id=sent_sound.audio.file_id,
        )

        logger.info(f"Создана новая запись сирены: {record}")
        await update.effective_message.reply_text(
            f"✅ Сирена «{record.title}» сохранена под slug «{record.slug}»."
        )

    async def build_siren_list(self, page: int) -> tuple[str, InlineKeyboardMarkup]:
        """
        Текст + клавиатура для страницы списка сирен: кнопки с сквозными
        номерами записей на этой странице (по 4 в ряд) + навигация внизу.
        Кнопки без действия (недоступный "назад" на первой странице,
        недоступный "вперёд" на последней) получают callback_data
        "siren_noop" — Telegram не умеет по-настоящему отключать инлайн-
        кнопки, поэтому неактивность имитируется отсутствием эффекта клика.
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
                    callback_data=f"siren_pick:{record.slug}",
                )
            )
            if len(row) == 4:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        has_prev = page > 0
        has_next = offset + SIREN_PAGE_SIZE < total

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

        text = f"У меня есть {total} записей. Выбери номер:"
        return text, InlineKeyboardMarkup(buttons)

    async def send_siren_record(self, message, user: WhirlUser, slug: str) -> None:
        """
        Общая логика: находит запись по slug, шлёт картинку+звук, отменяет
        прежние WAITING попытки пользователя и создаёт новую. Используется
        и из /get <slug>, и из клика по номеру в инлайн-списке — message
        может быть как Update.effective_message, так и CallbackQuery.message,
        у обоих есть reply_photo/reply_audio/reply_text.
        """
        try:
            record = await SirenRecord.objects.aget(slug=slug, is_active=True)
        except SirenRecord.DoesNotExist:
            await message.reply_text(f"❌ Сирена «{slug}» не найдена.")
            return

        try:
            image_asset = await SirenRecordImage.objects.aget(record=record)
            sound_asset = await SirenRecordSound.objects.aget(record=record)
        except (SirenRecordImage.DoesNotExist, SirenRecordSound.DoesNotExist):
            await message.reply_text(f"❌ Для сирены «{slug}» не найдены файлы.")
            return

        image_file_id = await image_asset.aget_file_id(self.app_bot_id, default=None)
        sound_file_id = await sound_asset.aget_file_id(self.app_bot_id, default=None)

        if not image_file_id or not sound_file_id:
            await message.reply_text(f"❌ Для сирены «{slug}» не найдены файлы этого бота.")
            return

        await message.reply_photo(photo=image_file_id)
        await message.reply_audio(audio=sound_file_id, title=record.title)

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

        await message.reply_text(
            "🎤 Теперь запиши голосовое — попробуй повторить этот паттерн."
        )

    async def handle_get(self, update: Update, context: CallbackContext) -> None:
        """
        /get <slug> — отправляет конкретную запись.
        /get без slug — присылает пронумерованный список с пагинацией.
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
        
        await self.send_siren_record(update.effective_message, user, args[0])

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
        
        slug = query.data.split(":", 1)[1]
        user = await self.get_or_create_virtual_user(update)
        await self.send_siren_record(query.message, user, slug)

    async def handle_siren_noop(self, update: Update, context: CallbackContext) -> None:
        """Клик по неактивной кнопке навигации — просто гасим "часики" на кнопке."""
        await update.callback_query.answer()
        
    async def handle_voice_reply(self, update: Update, context: CallbackContext) -> None:
        """
        Ловит голосовое сообщение пользователя. Если перед этим он
        запрашивал сирену через /get — считает это попыткой её повторить,
        строит normalized_curve из записи и рисует её тем же render_pattern_image,
        что и эталон.
        """
        user = await self.get_or_create_virtual_user(update)
        voice = update.effective_message.voice
        if voice is None:
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

        # 2) Скачиваем и разбираем голосовое
        tg_file = await context.bot.get_file(voice.file_id)
        buf = io.BytesIO()
        await tg_file.download_to_memory(buf)
        buf.seek(0)

        try:
            raw_curve = self.extract_normalized_curve(buf.read())
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

        smoothed = self.smooth_curve(raw_curve)
        thresholded = self.apply_threshold(smoothed)
        aligned = self.align_user_curve(record.normalized_curve, thresholded)
        score = self.compute_match_score(record.normalized_curve, aligned)

        await SirenAttempt.objects.filter(pk=attempt.pk).aupdate(
            status=SirenAttempt.Status.SUCCESS,
            score=score,
            user_curve=aligned,
            updated_at=timezone.now(),
        )

        # 3) Рисуем результат и редактируем то же сообщение
        image_bytes = self.render_comparison_image(record.normalized_curve, aligned)
        image_buf = io.BytesIO(image_bytes)
        image_buf.name = f"{record.slug}_attempt.png"

        caption = f"Твоя попытка повторить «{record.title}»: совпадение {score}%"

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
            # фолбэк — новым сообщением
            await update.effective_message.reply_photo(
                photo=image_buf,
                caption=caption,
            )
        
    def extract_normalized_curve(self, ogg_bytes: bytes, n_points: int = 200) -> list:
        """
        Декодирует голосовое (OGG/Opus) и строит нормализованную огибающую
        громкости в том же формате, что normalized_curve из generate_pattern:
        [{"t": ..., "rms": ..., "pitch": ...}, ...], чтобы её можно было
        отрисовать тем же render_pattern_image.
        Требует ffmpeg в системе (используется через pydub).
        """
        audio = AudioSegment.from_file(io.BytesIO(ogg_bytes), format="ogg")
        audio = audio.set_channels(1)

        samples = np.array(audio.get_array_of_samples()).astype(np.float32)
        sample_rate = audio.frame_rate
        duration = len(samples) / sample_rate

        if duration <= 0:
            raise ValueError("Пустая аудиозапись")

        # RMS-окна по ~30мс, без сторонних библиотек типа librosa
        window_size = max(int(sample_rate * 0.03), 1)
        n_windows = max(len(samples) // window_size, 1)

        rms = np.array([
            np.sqrt(np.mean(
                samples[i * window_size:(i + 1) * window_size].astype(np.float64) ** 2
            ) + 1e-9)
            for i in range(n_windows)
        ])

        rms_norm = (rms - rms.min()) / (rms.max() - rms.min() + 1e-9)

        # растягиваем на n_points, как в generate_pattern, чтобы формат
        # совпадал с эталонной кривой
        x_src = np.linspace(0, duration, n_windows)
        t = np.linspace(0, duration, n_points)
        rms_interp = np.interp(t, x_src, rms_norm)

        # без честного pitch-трекинга (librosa.pyin) держим синюю линию
        # на нуле — она у нас и так по умолчанию не обязательна
        pitch_flat = np.zeros_like(t)

        return [
            {"t": float(ti), "rms": float(ri), "pitch": float(pi)}
            for ti, ri, pi in zip(t, rms_interp, pitch_flat)
        ]

    def apply_threshold(self, curve: list, threshold: float = PEAK_THRESHOLD, key: str = "rms") -> list:
        """
        Шумовой гейт: значения key ниже threshold обнуляются, чтобы тишина
        и случайные шорохи в начале/конце голосового не участвовали
        в поиске пика и в расчёте несовпадения.
        """
        result = []
        for p in curve:
            p = dict(p)
            if p[key] < threshold:
                p[key] = 0.0
            result.append(p)
        return result

    def smooth_curve(self, curve: list, window: int = 9, key: str = "rms") -> list:
        """
        Сглаживает key скользящим средним (окно нечётное, симметричное).
        Нужно в первую очередь для пользовательской кривой — сырой RMS
        из voice-сообщения рваный даже при идеальном повторе паттерна,
        и эти зубцы срезают площадь пересечения при подсчёте IoU.
        Края паддим ближайшим значением, чтобы не проседали к нулю.
        """
        if window < 3 or window % 2 == 0:
            window = 9

        values = np.array([p[key] for p in curve])
        pad = window // 2
        padded = np.pad(values, pad, mode="edge")
        kernel = np.ones(window) / window
        smoothed = np.convolve(padded, kernel, mode="valid")

        result = []
        for p, s in zip(curve, smoothed):
            p = dict(p)
            p[key] = float(s)
            result.append(p)
        return result

    def find_first_peak(self, curve: list, threshold: float = PEAK_THRESHOLD, key: str = "rms") -> dict | None:
        """
        Первый локальный максимум key, превышающий threshold.
        Если чёткого локального максимума нет (пик на самом краю кривой) —
        берём первую точку, вообще превысившую порог.
        """
        values = [p[key] for p in curve]
        n = len(values)
        for i in range(1, n - 1):
            if values[i] < threshold:
                continue
            if values[i] >= values[i - 1] and values[i] >= values[i + 1] and values[i] > values[i - 1]:
                return curve[i]
        for p in curve:
            if p[key] >= threshold:
                return p
        return None

    def align_user_curve(self, target_curve: list, user_curve: list, threshold: float = PEAK_THRESHOLD) -> list:
        """
        Сдвигает user_curve по времени так, чтобы её первый пик rms совпал
        с первым пиком target_curve, и ресемплит на временную сетку target —
        дальше кривые сравнимы поточечно и рисуются в одних координатах.
        """
        target_peak = self.find_first_peak(target_curve, threshold)
        user_peak = self.find_first_peak(user_curve, threshold)

        shift = target_peak["t"] - user_peak["t"] if (target_peak and user_peak) else 0.0

        user_t = np.array([p["t"] for p in user_curve]) + shift
        user_rms = np.array([p["rms"] for p in user_curve])
        user_pitch = np.array([p.get("pitch", 0.0) for p in user_curve])
        target_t = np.array([p["t"] for p in target_curve])

        # вне диапазона user-кривой после сдвига (ещё не начала / уже
        # закончила) — честно считаем громкость нулевой
        rms_aligned = np.interp(target_t, user_t, user_rms, left=0.0, right=0.0)
        pitch_aligned = np.interp(target_t, user_t, user_pitch, left=0.0, right=0.0)

        return [
            {"t": float(ti), "rms": float(ri), "pitch": float(pi)}
            for ti, ri, pi in zip(target_t, rms_aligned, pitch_aligned)
        ]

    def compute_match_score(self, target_curve: list, user_curve_aligned: list, key: str = "rms") -> float:
        """
        Score = площадь пересечения / площадь объединения (IoU) по огибающей.
        В отличие от MSE, не завышается за счёт совместной тишины: пустые
        участки, где обе кривые ~0, не дают вклада ни в числитель, ни
        в знаменатель — учитывается только реально «звучащая» масса.
        """
        target = np.array([p[key] for p in target_curve])
        user = np.array([p[key] for p in user_curve_aligned])

        intersection = np.sum(np.minimum(target, user))
        union = np.sum(np.maximum(target, user))

        if union <= 1e-9:
            return 0.0

        return round(intersection / union * 100, 1)

    def render_comparison_image(self, target_curve: list, user_curve_aligned: list) -> bytes:
        """
        Эталон и попытка пользователя рисуются как полупрозрачные заливки
        от кривой до нуля — каждая своим бледным цветом. Там, где области
        перекрываются, альфа-блендинг даёт смешанный цвет, и рассинхрон
        виден сразу по форме и по чистым (неперекрытым) кускам заливки,
        без нужды сверяться с цифрой score.
        """
        width, height = 800, 300
        margin = 20
        bg_color = (18, 18, 24, 255)
        target_fill = (255, 140, 60, 110)    # бледно-оранжевый, полупрозрачный
        target_line = (255, 140, 60, 255)
        user_fill = (110, 220, 120, 110)     # бледно-зелёный, полупрозрачный
        user_line = (110, 220, 120, 255)
        axis_color = (70, 70, 80, 255)

        base = Image.new("RGBA", (width, height), bg_color)
        axis_draw = ImageDraw.Draw(base)
        axis_draw.line(
            [(margin, height // 2), (width - margin, height // 2)],
            fill=axis_color,
            width=1,
        )

        n = len(target_curve)
        if n < 2:
            buf = io.BytesIO()
            base.convert("RGB").save(buf, format="PNG")
            return buf.getvalue()

        plot_w = width - 2 * margin
        plot_h = height - 2 * margin
        baseline_y = margin + plot_h  # y соответствующий value=0

        def to_xy(i: int, value: float):
            x = margin + plot_w * (i / (n - 1))
            y = margin + plot_h * (1 - value)
            return x, y

        def draw_area(curve: list, key: str, fill_color, line_color):
            points = [to_xy(i, p[key]) for i, p in enumerate(curve)]
            polygon = [(margin, baseline_y)] + points + [(width - margin, baseline_y)]

            layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            layer_draw = ImageDraw.Draw(layer)
            layer_draw.polygon(polygon, fill=fill_color)
            layer_draw.line(points, fill=line_color, width=2, joint="curve")
            return layer

        target_layer = draw_area(target_curve, "rms", target_fill, target_line)
        user_layer = draw_area(user_curve_aligned, "rms", user_fill, user_line)

        base = Image.alpha_composite(base, target_layer)
        base = Image.alpha_composite(base, user_layer)

        buf = io.BytesIO()
        base.convert("RGB").save(buf, format="PNG")
        return buf.getvalue()

    # --- Генерация паттерна ---

    def generate_pattern(self, pattern: list[float] | None = None):
        """
        Генерирует процедурный паттерн сирены.

        :param pattern: необязательная "заготовка" волны, например [0,1,2,1,2,0,0].
            Если передана — паттерн повторяется 3 раза подряд и растягивается
            (линейной интерполяцией) на всю длительность сирены, а максимальное
            значение списка принимается за максимум огибающей амплитуды.
            Если None — амплитуда генерируется автоматически (синус со
            случайной частотой), как раньше.
        Возвращает (generated_sequence, normalized_curve), оба —
        JSON-сериализуемые списки точек, готовые для полей модели.
        """
        duration = 5.0
        n_points = 200
        t = np.linspace(0, duration, n_points)

        base_freq = np.random.uniform(0.5, 1.5)
        pitch = 400 + 200 * np.sin(2 * np.pi * base_freq * t)

        if pattern:
            pattern_arr = np.asarray(pattern, dtype=float)
            pattern_max = pattern_arr.max()
            if pattern_max <= 0:
                pattern_max = 1.0

            repeated = np.tile(pattern_arr, 3)
            x_repeated = np.linspace(0, duration, repeated.size)

            shape = np.interp(t, x_repeated, repeated) / pattern_max
            shape = np.clip(shape, 0.0, 1.0)

            amplitude = shape

            # питч следует той же самой форме, что и амплитуда —
            # никакой отдельной "заморозки" и скачков, просто другой
            # диапазон значений (Гц вместо 0..1)
            pitch_base = 400
            pitch_range = 200
            pitch = pitch_base + pitch_range * shape
        else:
            amplitude = 0.5 + 0.5 * np.abs(np.sin(2 * np.pi * base_freq * t))
            pitch = 400 + 200 * np.sin(2 * np.pi * base_freq * t)

        generated_sequence = [
            {"t": float(ti), "amplitude": float(ai), "pitch": float(pi)}
            for ti, ai, pi in zip(t, amplitude, pitch)
        ]

        amp_norm = (amplitude - amplitude.min()) / (
            amplitude.max() - amplitude.min() + 1e-9
        )
        pitch_norm = (pitch - pitch.min()) / (pitch.max() - pitch.min() + 1e-9)

        normalized_curve = [
            {"t": float(ti), "rms": float(ri), "pitch": float(pi)}
            for ti, ri, pi in zip(t, amp_norm, pitch_norm)
        ]

        return generated_sequence, normalized_curve

    def render_pattern_image(self, normalized_curve: list) -> bytes:
        """
        Рисует PNG с огибающей громкости (оранжевая линия) и, если есть,
        pitch-контуром (синяя линия) поверх нормализованной кривой [0, 1].
        """
        width, height = 800, 300
        margin = 20
        bg_color = (18, 18, 24)
        amp_color = (255, 140, 60)
        pitch_color = (90, 170, 255)
        axis_color = (70, 70, 80)

        img = Image.new("RGB", (width, height), bg_color)
        draw = ImageDraw.Draw(img)
        draw.line(
            [(margin, height // 2), (width - margin, height // 2)],
            fill=axis_color,
            width=1,
        )

        n = len(normalized_curve)
        if n < 2:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()

        plot_w = width - 2 * margin
        plot_h = height - 2 * margin

        def to_xy(i: int, value: float):
            x = margin + plot_w * (i / (n - 1))
            y = margin + plot_h * (1 - value)
            return x, y

        amp_points = [to_xy(i, p["rms"]) for i, p in enumerate(normalized_curve)]
        draw.line(amp_points, fill=amp_color, width=3, joint="curve")

        if "pitch" in normalized_curve[0]:
            pitch_points = [
                to_xy(i, p["pitch"]) for i, p in enumerate(normalized_curve)
            ]
            draw.line(pitch_points, fill=pitch_color, width=2, joint="curve")

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def render_pattern_sound(self, generated_sequence: list) -> bytes:
        """
        Синтезирует эталонный звук по generated_sequence: амплитуда и
        частота интерполируются по времени, частота интегрируется в фазу
        (а не просто sin(2*pi*f*t)), чтобы плавающий pitch не давал щелчков.
        Возвращает моно WAV 16-bit PCM как bytes.
        """
        sample_rate = 44100

        n = len(generated_sequence)
        if n < 2:
            raise ValueError("generated_sequence слишком короткая для синтеза звука")

        t_points = np.array([p["t"] for p in generated_sequence])
        amp_points = np.array([p["amplitude"] for p in generated_sequence])
        freq_points = np.array([p.get("pitch", 440.0) for p in generated_sequence])

        duration = t_points[-1] - t_points[0]
        n_samples = max(int(duration * sample_rate), 2)
        t_samples = np.linspace(t_points[0], t_points[-1], n_samples)

        amp_env = np.interp(t_samples, t_points, amp_points)
        freq_env = np.interp(t_samples, t_points, freq_points)

        dt = 1.0 / sample_rate
        phase = 2 * np.pi * np.cumsum(freq_env) * dt
        waveform = amp_env * np.sin(phase)

        # нормализация громкости + короткий fade in/out на краях, чтобы
        # не было щелчка в начале/конце файла
        peak = np.max(np.abs(waveform))
        if peak > 0:
            waveform = waveform / peak

        fade_len = int(0.01 * sample_rate)
        if 0 < fade_len < n_samples // 2:
            fade_in = np.linspace(0, 1, fade_len)
            fade_out = np.linspace(1, 0, fade_len)
            waveform[:fade_len] *= fade_in
            waveform[-fade_len:] *= fade_out

        pcm = (waveform * 32767 * 0.9).astype(np.int16)

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm.tobytes())

        return buf.getvalue()
