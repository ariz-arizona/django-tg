from django.db import models
from .base import bot_prefix


class UserReading(models.Model):
    # Основной инструмент гадания
    class ReadingCategory(models.TextChoices):
        ONE = "one", "Одна карта"                      # Для команды /one
        TAROT = "tarot", "Таро"                        # Для /card, /card3
        ORACLE = "oracle", "Оракул"                    # Для /oraculum, /oraculum3
        RUNES = "runes", "Руны (Футарк)"               # Для /futark, /futark triplet
        CANVAS_SPREAD = "canvas_spread", "Расклад на холсте" # Для /spread
        ALL = "all", "Вся колода"

    class ReadingStatus(models.TextChoices):
        PENDING = "pending", "Ожидание"
        INITIALIZING = "initializing", "Инициализация"
        LOADING = "loading", "Загрузка карт"
        RENDERING = "rendering", "Создание изображения"
        UPLOADING = "uploading", "Отправка"
        SUCCESS = "success", "Успешно"
        ERROR = "error", "Ошибка"
        
    user = models.ForeignKey(
        "tg_bot.TgUser",
        on_delete=models.SET_NULL,
        related_name="user_readings",
        null=True,
        verbose_name="Пользователь",
    )
    
    # Поля типов
    category = models.CharField(
        max_length=20,
        choices=ReadingCategory.choices,
        default=ReadingCategory.TAROT,
        verbose_name="Категория гадания",
    )
    
    reading_status = models.CharField(
        max_length=20,
        choices=ReadingStatus.choices,
        default=ReadingStatus.PENDING,
        verbose_name="Статус выполнения",
    )

    # Дополнительные настройки расклада (флаги)
    is_flipped_allowed = models.BooleanField(
        default=False, verbose_name="С перевернутыми"
    )  # Для флага flip
    is_major_only = models.BooleanField(
        default=False, verbose_name="Только Старшие Арканы"
    )  # Для флага major
    deck_id = models.IntegerField(
        null=True, blank=True, verbose_name="ID колоды"
    )  # Для аргумента deck НОМЕР
    count = models.PositiveSmallIntegerField(
        default=1, 
        verbose_name="Количество карт/рун"
    )
    
    original_query = models.TextField(
        blank=True, 
        default="", 
        verbose_name="Оригинальный запрос",
        help_text="Вопрос или тема, которую ввел пользователь перед гаданием"
    )

    created_at = models.DateTimeField(
        auto_now_add=True, 
        verbose_name="Дата гадания"  # Оставляем понятное имя для админки
    )
    updated_at = models.DateTimeField(
        auto_now=True, 
        verbose_name="Дата изменения"
    )
    
    bot = models.ForeignKey(
        "tg_bot.Bot",
        on_delete=models.SET_NULL,
        related_name="user_readings",
        null=True,
        blank=True,
        verbose_name="Бот",
        help_text="Бот, через который выполнено гадание"
    )
    
    # Основные данные
    text = models.TextField(blank=True, verbose_name="Текст гадания")
    message_id = models.IntegerField(null=True, blank=True, verbose_name="ID сообщения")
    
    card_ids = models.JSONField(
        default=list,
        blank=True,
        verbose_name="ID выпавших карт/рун",
        help_text="Список идентификаторов карт в формате JSON, например: [12, 45, 3]",
    )

    def __str__(self):
        name = self.user.username or self.user.first_name or str(self.user.tg_id)
        return f"{self.get_category_display()} - {name}"

    class Meta:
        verbose_name = f"{bot_prefix}: Пользовательское гадание"
        verbose_name_plural = f"{bot_prefix}: Пользовательские гадания"
        
        # Индексы для быстрой фильтрации по категориям и датам
        indexes = [
            models.Index(fields=["user", "category", "created_at"]),
        ]

class AIReadingInterpretation(models.Model):
    class AIStatus(models.TextChoices):
        PENDING = "pending", "В очереди / Обрабатывается"
        SUCCESS = "success", "Успешно завершено"
        FAILED = "failed", "Ошибка генерации"

    # Связь с основным раскладом (один расклад может иметь несколько ИИ-толкований)
    reading = models.ForeignKey(
        "UserReading",
        on_delete=models.CASCADE,
        related_name="ai_interpretations",
        verbose_name="Расклад карт",
    )
    
    # Какой ключ и провайдер выполняли этот запрос
    ai_key = models.ForeignKey(
        "AIApiKey",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="interpretations",
        verbose_name="Использованный API Ключ",
    )
    
    # Название модели, которая фактически ответила (например, 'gemini-2.0-flash')
    model_used = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name="Использованная модель",
    )

    status = models.CharField(
        max_length=15,
        choices=AIStatus.choices,
        default=AIStatus.PENDING,
        verbose_name="Статус запроса",
    )

    # --- Слои данных (Промпт / Ответ / Ошибка) ---
    prompt_system = models.TextField(
        blank=True, 
        verbose_name="Системный промпт (Инструкции)",
    )
    prompt_user = models.TextField(
        blank=True, 
        verbose_name="Пользовательский промпт / Контекст",
        help_text="Сюда входят выпавшие карты, вопрос юзера и т.д."
    )
    error_message = models.TextField(
        blank=True, 
        verbose_name="Текст ошибки",
        help_text="Traceback или описание ошибки API в случае FAILED"
    )

    # --- Счетчик токенов ---
    prompt_tokens = models.PositiveIntegerField(
        default=0, 
        verbose_name="Входящие токены (Prompt)"
    )
    completion_tokens = models.PositiveIntegerField(
        default=0, 
        verbose_name="Исходящие токены (Completion)"
    )
    total_tokens = models.PositiveIntegerField(
        default=0, 
        verbose_name="Всего токенов"
    )

    # Временные метки
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создан запрос")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлен")

    class Meta:
        verbose_name = "ИИ-Интерпретация расклада"
        verbose_name_plural = "ИИ-Интерпретации раскладов"
        indexes = [
            models.Index(fields=["reading", "status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"Интерпретация #{self.id} для расклада #{self.reading_id} [{self.get_status_display()}]"
    
class AIReadingPage(models.Model):
    interpretation = models.ForeignKey(
        "AIReadingInterpretation",
        on_delete=models.CASCADE,
        related_name="pages",
        verbose_name="ИИ-Интерпретация",
    )
    content = models.TextField(verbose_name="Часть текста")
    page_number = models.PositiveIntegerField(verbose_name="Порядковый номер чанка")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Часть текста ответа"
        ordering = ["page_number"]
        indexes = [
            models.Index(fields=["interpretation", "page_number"]),
        ]

    def __str__(self):
        return f"Чанк {self.page_number} для интерпретации {self.interpretation_id}"