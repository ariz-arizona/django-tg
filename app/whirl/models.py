from django.db import models
from django.utils.text import slugify

from django.contrib.contenttypes.fields import GenericRelation

from tg_bot.models import BotFile, BotFileMixin


class WhirlUser(models.Model):
    """
    Пользователь бота "Угадай сирену".
    """

    user = models.OneToOneField(
        "tg_bot.TgUser",
        on_delete=models.CASCADE,
        related_name="whirl",
        verbose_name="Пользователь Telegram",
    )

    is_admin = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Пользователь Сирены"
        verbose_name_plural = "Пользователи Сирены"

    def __str__(self):
        return f"WhirlUser({self.user})"


class SirenRecord(models.Model):
    """
    Эталонная запись сирены: сгенерированный паттерн + нормализованные
    данные для последующего сравнения с голосовым сообщением игрока.
    Сами файлы (картинка/звук) хранятся не тут, а в SirenRecordImage
    и SirenRecordSound (см. ниже) — каждая через свою GenericRelation
    к BotFile.
    """

    slug = models.SlugField(max_length=64, unique=True, blank=True)
    title = models.CharField(max_length=128)

    created_by = models.ForeignKey(
        WhirlUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="siren_records",
    )

    # Сырые данные генерации: список точек паттерна.
    # numpy-массив в JSONField не хранится напрямую — сериализуется в
    # список словарей перед сохранением (см. generate_pattern в bot.py).
    generated_sequence = models.JSONField(
        help_text='Список точек генерации: [{"t": .., "amplitude": .., "pitch": ..}, ...]',
    )

    # То, что реально участвует в сравнении с записью игрока
    # (cross-correlation по RMS, pitch-контур и т.д.) — amplitude/pitch
    # приведены к диапазону [0, 1].
    normalized_curve = models.JSONField(
        help_text='Точки оценки: [{"t": .., "rms": .., "pitch": ..}, ...]',
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Запись сирены"
        verbose_name_plural = "Записи сирен"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} ({self.slug})"

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.title) or "siren"
            slug = base_slug
            i = 1
            while SirenRecord.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                i += 1
                slug = f"{base_slug}-{i}"
            self.slug = slug
        super().save(*args, **kwargs)


class SirenRecordImage(BotFileMixin, models.Model):
    """
    Картинка-визуализация паттерна для одной SirenRecord.
    Отдельная модель — значит свой ContentType, значит GenericRelation
    к BotFile тут никогда не пересечётся с SirenRecordSound ниже.
    """

    record = models.OneToOneField(
        SirenRecord,
        on_delete=models.CASCADE,
        related_name="image_asset",
    )
    files = GenericRelation(
        BotFile,
        related_query_name="siren_record_image",
        verbose_name="Картинка паттерна",
    )

    class Meta:
        verbose_name = "Картинка сирены"
        verbose_name_plural = "Картинки сирен"

    def __str__(self):
        return f"Картинка: {self.record.slug}"


class SirenRecordSound(BotFileMixin, models.Model):
    """Эталонное аудио паттерна для одной SirenRecord."""

    record = models.OneToOneField(
        SirenRecord,
        on_delete=models.CASCADE,
        related_name="sound_asset",
    )
    files = GenericRelation(
        BotFile,
        related_query_name="siren_record_sound",
        verbose_name="Аудио паттерна",
    )

    class Meta:
        verbose_name = "Звук сирены"
        verbose_name_plural = "Звуки сирен"

    def __str__(self):
        return f"Звук: {self.record.slug}"

class SirenAttempt(models.Model):
    """
    Попытка пользователя повторить паттерн сирены. Единственное хранилище
    состояния "ждём голосового" — context.user_data не используется.
    Создаётся со статусом WAITING сразу по /get. Если пользователь
    запрашивает /get заново, не ответив голосовым, — все его прежние
    WAITING попытки переводятся в CANCELLED, отвечать на них уже нельзя.
    На одну SirenRecord у пользователя может быть сколько угодно попыток
    (успешных, отменённых) — ограничения на количество нет.
    """

    class Status(models.TextChoices):
        WAITING = "waiting", "Ожидает голосового"
        PROCESSING = "processing", "Обрабатывается"
        SUCCESS = "success", "Разобрана"
        CANCELLED = "cancelled", "Отменена"

    user = models.ForeignKey(
        WhirlUser,
        on_delete=models.CASCADE,
        related_name="attempts",
        verbose_name="Пользователь",
    )

    record = models.ForeignKey(
        SirenRecord,
        on_delete=models.CASCADE,
        related_name="attempts",
        verbose_name="Сирена",
    )

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.WAITING,
    )

    score = models.FloatField(
        null=True,
        blank=True,
        help_text="IoU-совпадение с эталоном, 0..100. Заполняется при переходе в SUCCESS.",
    )

    user_curve = models.JSONField(
        null=True,
        blank=True,
        help_text='Выровненная кривая попытки: [{"t": .., "rms": .., "pitch": ..}, ...]. Заполняется при переходе в SUCCESS.',
    )
    
    reply_message_id = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="ID сообщения-приглашения ('запиши голосовое'), которое редактируется при ответе.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Попытка"
        verbose_name_plural = "Попытки"
        ordering = ["-created_at"]

    def __str__(self):
        if self.status == self.Status.SUCCESS:
            return f"{self.user} → {self.record.slug}: {self.score}%"
        return f"{self.user} → {self.record.slug}: {self.get_status_display()}"