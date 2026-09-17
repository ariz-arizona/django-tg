import io
import wave

import numpy as np
from PIL import Image, ImageDraw
from pydub import AudioSegment

from telegram import Update
from telegram.ext import CommandHandler, MessageHandler, CallbackContext, filters

from tg_bot.bot.abstract import AbstractBot
from server.logger import logger

from tg_bot.models import TgUser, BotFile
from whirl.models import WhirlUser, SirenRecord, SirenRecordImage, SirenRecordSound


class WhirlBot(AbstractBot):
    def __init__(self):
        self.handlers = self.get_handlers()

    def get_handlers(self):
        return [
            CommandHandler("start", self.handle_start, filters.ChatType.PRIVATE),
            CommandHandler("create", self.handle_create, filters.ChatType.PRIVATE),
            CommandHandler("get", self.handle_get, filters.ChatType.PRIVATE),
            MessageHandler(filters.VOICE, self.handle_voice_reply)
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

    async def handle_get(self, update: Update, context: CallbackContext) -> None:
        """
        /get <slug>
        Присылает картинку и звук эталона по slug, и запоминает,
        что пользователь ждёт разбора своего голосового именно
        по этой сирене.
        """
        args = context.args
        if not args:
            await update.effective_message.reply_text("Использование: /get <slug>")
            return

        slug = args[0]

        try:
            record = await SirenRecord.objects.aget(slug=slug, is_active=True)
        except SirenRecord.DoesNotExist:
            await update.effective_message.reply_text(f"❌ Сирена «{slug}» не найдена.")
            return

        try:
            image_asset = await SirenRecordImage.objects.aget(record=record)
            sound_asset = await SirenRecordSound.objects.aget(record=record)
        except (SirenRecordImage.DoesNotExist, SirenRecordSound.DoesNotExist):
            await update.effective_message.reply_text(
                f"❌ Для сирены «{slug}» не найдены файлы."
            )
            return

        image_file_id = await image_asset.aget_file_id(self.app_bot_id, default=None)
        sound_file_id = await sound_asset.aget_file_id(self.app_bot_id, default=None)

        if not image_file_id or not sound_file_id:
            await update.effective_message.reply_text(
                f"❌ Для сирены «{slug}» не найдены файлы этого бота."
            )
            return

        await update.effective_message.reply_photo(photo=image_file_id)
        await update.effective_message.reply_audio(
            audio=sound_file_id,
            title=record.title,
        )

        # запоминаем, какую сирену пользователь сейчас пытается повторить —
        # следующее голосовое от него будет разобрано именно по ней
        context.user_data["awaiting_siren_id"] = record.id

        await update.effective_message.reply_text(
            "🎤 Теперь запиши голосовое — попробуй повторить этот паттерн."
        )
        
    async def handle_voice_reply(self, update: Update, context: CallbackContext) -> None:
        """
        Ловит голосовое сообщение пользователя. Если перед этим он
        запрашивал сирену через /get — считает это попыткой её повторить,
        строит normalized_curve из записи и рисует её тем же render_pattern_image,
        что и эталон.
        """
        siren_id = context.user_data.get("awaiting_siren_id")
        if siren_id is None:
            # голосовое пришло не в ответ на /get — игнорируем молча,
            # чтобы бот не реагировал на случайные войсы
            return

        voice = update.effective_message.voice
        if voice is None:
            return

        try:
            record = await SirenRecord.objects.aget(id=siren_id)
        except SirenRecord.DoesNotExist:
            context.user_data.pop("awaiting_siren_id", None)
            await update.effective_message.reply_text(
                "❌ Сирена, на которую ты отвечала, больше не существует."
            )
            return

        tg_file = await context.bot.get_file(voice.file_id)
        buf = io.BytesIO()
        await tg_file.download_to_memory(buf)
        buf.seek(0)

        try:
            normalized_curve = self.extract_normalized_curve(buf.read())
        except Exception as e:
            logger.error(f"Ошибка разбора голосового: {e}", exc_info=True)
            await update.effective_message.reply_text(
                "❌ Не удалось разобрать голосовое сообщение."
            )
            return

        image_bytes = self.render_pattern_image(normalized_curve)
        image_buf = io.BytesIO(image_bytes)
        image_buf.name = f"{record.slug}_attempt.png"

        await update.effective_message.reply_photo(
            photo=image_buf,
            caption=f"Твоя попытка повторить «{record.title}»",
        )

        # сбрасываем ожидание — следующее голосовое уже не будет разобрано,
        # пока пользователь не вызовет /get заново
        context.user_data.pop("awaiting_siren_id", None)

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
