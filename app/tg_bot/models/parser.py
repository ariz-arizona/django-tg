from django.db import models
from tg_bot.models import TgUser

bot_prefix = "Card Parser"


class ParseProduct(models.Model):
    """Модель для хранения данных о продукте."""

    PRODUCT_TYPE_CHOICES = [
        ("ozon", "Ozon"),
        ("wb", "Wildberries"),
    ]

    product_id = models.CharField(max_length=255, verbose_name="ID товара")
    photo_id = models.CharField(max_length=255, verbose_name="ID фото в Telegram")
    caption = models.TextField(verbose_name="Подпись к фото")
    product_type = models.CharField(
        max_length=10, choices=PRODUCT_TYPE_CHOICES, verbose_name="Тип продукта"
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    def __str__(self):
        return f"{self.get_product_type_display()} - {self.caption[:30]}"

    class Meta:
        verbose_name = f"{bot_prefix}: Продукт"
        verbose_name_plural = f"{bot_prefix}: Продукты"


class TgUserProduct(models.Model):
    """Модель для связи пользователя с продуктом."""

    tg_user = models.ForeignKey(
        TgUser,
        on_delete=models.CASCADE,
        related_name="user_products",
        verbose_name="Пользователь",
    )
    product = models.ForeignKey(
        ParseProduct,
        on_delete=models.CASCADE,
        related_name="product_users",
        verbose_name="Продукт",
    )
    sent_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата отправки")

    class Meta:
        verbose_name = f"{bot_prefix}: Продукт пользователя"
        verbose_name_plural = f"{bot_prefix}: Продукты пользователей"
        unique_together = (
            "tg_user",
            "product",
        )  # Один и тот же пользователь может отправить продукт только один раз.

    def __str__(self):
        return f"{self.tg_user} -> {self.product}"
