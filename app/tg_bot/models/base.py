from django.db import models

class Bot(models.Model):
    name = models.CharField(max_length=100, verbose_name="Название бота")
    token = models.CharField(max_length=255, verbose_name="Токен")
    chat_id = models.CharField(max_length=50, verbose_name="Chat ID")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    def __str__(self):
        return self.name

class TgUser(models.Model):
    """Модель для хранения данных о пользователе Telegram."""
    tg_id = models.BigIntegerField(unique=True, verbose_name="Telegram ID")
    username = models.CharField(max_length=255, blank=True, null=True, verbose_name="Username")
    first_name = models.CharField(max_length=255, blank=True, null=True, verbose_name="Имя")
    last_name = models.CharField(max_length=255, blank=True, null=True, verbose_name="Фамилия")
    language_code = models.CharField(max_length=10, blank=True, null=True, verbose_name="Язык пользователя")
    is_bot = models.BooleanField(default=False, verbose_name="Это бот")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    def __str__(self):
        return f"{self.username or self.first_name or 'Пользователь'} ({self.tg_id})"