import io
import wave

import numpy as np
from PIL import Image, ImageDraw

class RenderingMixin:
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
