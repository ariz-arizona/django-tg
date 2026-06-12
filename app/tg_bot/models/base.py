from django.db import models


class Bot(models.Model):
    # Типы ботов
    BOT_TYPE_CHOICES = [
        ("ParserBot", "ParserBot"),
        ("TarotBot", "TarotBot"),
        ("GachaBot", "GachaBot")
    ]

    name = models.CharField(max_length=100, verbose_name="Название бота")
    username = models.CharField(
        max_length=100,
        unique=True,
        verbose_name="Username бота",
        help_text="Telegram username бота (например: my_test_bot)",
        editable=False,  # Не редактируется в админке
        blank=True,
        null=True
    )
    token = models.CharField(max_length=255, verbose_name="Токен")
    chat_id = models.CharField(max_length=50, verbose_name="Chat ID")
    bot_type = models.CharField(
        max_length=50,
        choices=BOT_TYPE_CHOICES,
        default="ParserBot",
        verbose_name="Тип бота",
    )
    is_enabled = models.BooleanField(default=True, verbose_name="Включен")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")
    docker_instance_name = models.CharField(
        max_length=100, 
        null=True, 
        blank=True, 
        verbose_name="Имя докер-инстанса",
        help_text="Укажите имя контейнера, который должен обрабатывать этого бота"
    )

    def __str__(self):
        if self.username:
            return f"@{self.username}"
        return self.name

    class Meta:
        verbose_name = "Бот"
        verbose_name_plural = "Боты"


class TgUser(models.Model):
    """Модель для хранения данных о пользователе Telegram."""

    tg_id = models.BigIntegerField(unique=True, verbose_name="Telegram ID")
    username = models.CharField(
        max_length=255, blank=True, null=True, verbose_name="Username"
    )
    first_name = models.CharField(
        max_length=255, blank=True, null=True, verbose_name="Имя"
    )
    last_name = models.CharField(
        max_length=255, blank=True, null=True, verbose_name="Фамилия"
    )
    language_code = models.CharField(
        max_length=10, blank=True, null=True, verbose_name="Язык пользователя"
    )
    is_bot = models.BooleanField(default=False, verbose_name="Это бот")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    def __str__(self):
        return f"{self.username or self.first_name or 'Пользователь'} ({self.tg_id})"
