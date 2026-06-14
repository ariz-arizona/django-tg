from django.db import models


from django.db import models


class AIApiKey(models.Model):
    # Заготовки для популярных API-эндпоинтов, чтобы не писать руками каждый раз
    class ProviderUrl(models.TextChoices):
        GOOGLE = "https://generativelanguage.googleapis.com/v1beta", "Официальный Google AI Studio (по умолчанию)"
        OPENROUTER = "https://openrouter.ai/api/v1", "OpenRouter"
        DEEPSEEK = "https://api.deepseek.com/v1", "DeepSeek API"
        LOCAL_OLLAMA = "http://localhost:11434/v1", "Локальный Ollama (Localhost)"
        CUSTOM = "custom", "Свой URL (указать в поле ниже)"

    bot = models.ForeignKey(
        "tg_bot.Bot",
        on_delete=models.CASCADE,
        related_name="api_keys",
        verbose_name="Бот",
    )
    
    title = models.CharField(
        max_length=100,
        verbose_name="Название / Описание",
        help_text="Например: 'DeepSeek для тестов' или 'Gemini Аккаунт 1'",
    )
    
    api_key = models.TextField(
        verbose_name="API Ключ",
        help_text="Ключ от выбранного провайдера (AIzaSy..., sk-..., и т.д.)",
    )

    # 1. Выбор из списка
    provider = models.CharField(
        max_length=100,
        choices=ProviderUrl.choices,
        default=ProviderUrl.GOOGLE,
        verbose_name="Провайдер API",
    )

    # 2. Поле для ввода вручную, если в списке выбран "CUSTOM"
    custom_base_url = models.URLField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name="Кастомный Базовый URL",
        help_text="Заполняется только если выбран вариант 'Свой URL'. Например: https://api.vllm-server.com/v1",
    )

    # 3. Переопределение модели для этого конкретного ключа
    override_model_name = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name="Переопределить модель",
        help_text="Если пусто, берется дефолтная ('gemini-2.0-flash'). Для других провайдеров укажите их модель, например: 'deepseek-chat' или 'meta-llama/llama-3'",
    )
    
    project_identifier = models.CharField(
        max_length=100, blank=True, verbose_name="Идентификатор проекта"
    )
    
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    is_exhausted = models.BooleanField(default=False, verbose_name="Лимит исчерпан")
    exhausted_until = models.DateTimeField(
        null=True, blank=True, verbose_name="Заблокирован до"
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Добавлен")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлен")

    class Meta:
        verbose_name = "API Ключ AI"
        verbose_name_plural = "API Ключи AI"
        indexes = [
            models.Index(fields=["bot", "is_active", "is_exhausted"]),
        ]

    # Свойство (property) для удобного получения финального URL в коде
    @property
    def final_base_url(self) -> str | None:
        """Возвращает итоговый URL для инициализации клиента."""
        if self.provider == self.ProviderUrl.CUSTOM:
            return self.custom_base_url
        return self.provider if self.provider else None

    def __str__(self):
        status = "Активен" if self.is_active and not self.is_exhausted else "Недоступен"
        prov_name = self.get_provider_display()
        return f"{self.title} ({prov_name}) — {status}"