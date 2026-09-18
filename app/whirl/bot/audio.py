import io

import numpy as np
from pydub import AudioSegment

PEAK_THRESHOLD = 0.15 

class AudioMixin:
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
