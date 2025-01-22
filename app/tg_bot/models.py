from django.db import models


class Bot(models.Model):
    name = models.CharField(max_length=100, verbose_name="Название бота")
    token = models.CharField(max_length=255, verbose_name="Токен")
    chat_id = models.CharField(max_length=50, verbose_name="Chat ID")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    def __str__(self):
        return self.name

from django.db import models

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


class ParseProduct(models.Model):
    """Модель для хранения данных о продукте."""
    PRODUCT_TYPE_CHOICES = [
        ('ozon', 'Ozon'),
        ('wb', 'Wildberries'),
    ]

    product_id = models.CharField(max_length=255, verbose_name="ID товара")
    photo_id = models.CharField(max_length=255, verbose_name="ID фото в Telegram")
    caption = models.TextField(verbose_name="Подпись к фото")
    product_type = models.CharField(max_length=10, choices=PRODUCT_TYPE_CHOICES, verbose_name="Тип продукта")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    def __str__(self):
        return f"{self.get_product_type_display()} - {self.caption[:30]}"


class TgUserProduct(models.Model):
    """Модель для связи пользователя с продуктом."""
    tg_user = models.ForeignKey(TgUser, on_delete=models.CASCADE, related_name="user_products", verbose_name="Пользователь")
    product = models.ForeignKey(ParseProduct, on_delete=models.CASCADE, related_name="product_users", verbose_name="Продукт")
    sent_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата отправки")

    class Meta:
        unique_together = ('tg_user', 'product')  # Один и тот же пользователь может отправить продукт только один раз.

    def __str__(self):
        return f"{self.tg_user} -> {self.product}"
