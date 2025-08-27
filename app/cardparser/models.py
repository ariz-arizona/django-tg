from asgiref.sync import sync_to_async
from django.db import models
from tg_bot.models import TgUser

bot_prefix = "Card Parser"

PRODUCT_TYPE_CHOICES = [
    ("ozon", "Ozon"),
    ("wb", "Wildberries"),
]

class Brand(models.Model):
    """Модель бренда с учётом принадлежности к платформе"""

    name = models.CharField(max_length=255, verbose_name="Название бренда")
    brand_id = models.CharField(max_length=50, verbose_name="Внешний ID бренда")
    product_type = models.CharField(
        max_length=10, choices=PRODUCT_TYPE_CHOICES, verbose_name="Тип площадки"
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        verbose_name = f"{bot_prefix}: Бренд"
        verbose_name_plural = f"{bot_prefix}: Бренды"
        # Уникальность: один бренд на одной платформе
        unique_together = ("brand_id", "product_type")
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.get_product_type_display()})"


class Category(models.Model):
    """Модель категории с привязкой к платформе"""

    name = models.CharField(max_length=255, verbose_name="Название категории")
    subject_id = models.IntegerField(verbose_name="subjectId / category_id")
    product_type = models.CharField(
        max_length=10, choices=PRODUCT_TYPE_CHOICES, verbose_name="Тип площадки"
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    class Meta:
        verbose_name = f"{bot_prefix}: Категория"
        verbose_name_plural = f"{bot_prefix}: Категории"
        unique_together = ("subject_id", "product_type")
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.get_product_type_display()})"


class ParseProduct(models.Model):
    """Модель для хранения данных о продукте."""

    product_id = models.CharField(max_length=255, verbose_name="ID товара")
    caption = models.TextField(verbose_name="Подпись к фото")
    name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name="Название товара",
        help_text="Официальное название товара (опционально)"
    )
    product_type = models.CharField(
        max_length=10, choices=PRODUCT_TYPE_CHOICES, verbose_name="Тип продукта"
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    brand = models.ForeignKey(
        Brand,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Бренд",
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Категория",
    )

    def __str__(self):
        return f"{self.get_product_type_display()} - {self.caption[:30]}"

    class Meta:
        verbose_name = f"{bot_prefix}: Продукт"
        verbose_name_plural = f"{bot_prefix}: Продукты"


class ProductImage(models.Model):
    """Модель для хранения изображений товара (Telegram file_id или прямая ссылка)"""

    IMAGE_TYPE_CHOICES = [
        ("telegram", "Telegram file_id"),
        ("link", "Прямая ссылка"),
    ]

    image_type = models.CharField(
        max_length=10, choices=IMAGE_TYPE_CHOICES, verbose_name="Тип изображения"
    )

    # Один из двух будет заполнен
    file_id = models.CharField(
        max_length=500,  # Telegram file_id может быть длинным
        blank=True,
        null=True,
        verbose_name="ID изображения в Telegram",
    )
    url = models.URLField(
        max_length=1000,
        blank=True,
        null=True,
        verbose_name="Прямая ссылка на изображение",
    )

    # Связь с товаром
    product = models.ForeignKey(
        ParseProduct,
        on_delete=models.CASCADE,
        related_name="images",
        verbose_name="Товар",
    )

    # Аудит
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    class Meta:
        verbose_name = f"{bot_prefix}: Изображение товара"
        verbose_name_plural = f"{bot_prefix}: Изображения товаров"
        ordering = ["-created_at"]

    def __str__(self):
        if self.image_type == "telegram":
            return f"📷 {self.file_id[:20]}... (Telegram)"
        return f"🔗 {self.url[:30]}... (Link)"


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

    def __str__(self):
        return f"{self.tg_user} -> {self.product}"

class BotSettings(models.Model):
    """
    Глобальные настройки бота. Только одна запись может быть активной.
    Ограничение enforced на уровне базы данных.
    """
    active = models.BooleanField(
        default=False,
        verbose_name="Активные настройки",
        help_text="Только одна запись в системе может быть активной."
    )

    picture_chat_id = models.CharField(
        max_length=50,
        verbose_name="Chat ID для загрузки картинок",
        help_text="Telegram chat ID, куда бот будет отправлять изображения для получения file_id"
    )

    parser_url_ozon = models.URLField(
        blank=True, null=True,
        verbose_name="URL парсера Ozon",
        help_text="API или веб-адрес для парсинга товаров Ozon"
    )

    parser_url_wb = models.URLField(
        blank=True, null=True,
        verbose_name="URL парсера Wildberries",
        help_text="API или веб-адрес для парсинга товаров Wildberries"
    )

    marketing_group_id = models.CharField(
        max_length=50,
        verbose_name="Группа для вывода маркетинга",
        help_text="Telegram chat ID группы, куда отправляются популярные товары"
    )

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Последнее обновление")

    class Meta:
        verbose_name = f"{bot_prefix}: Настройки бота"
        verbose_name_plural = f"{bot_prefix}: Настройки бота"
        ordering = ["-created_at"]

        # 🔒 Ограничения на уровне БД
        constraints = [
            # 1. Гарантируем, что active=True может быть только у одной записи
            models.UniqueConstraint(
                fields=['active'],
                condition=models.Q(active=True),
                name='unique_active_settings'
            ),
        ]

    def __str__(self):
        return f"Активно ✅" if self.active else "Неактивно ❌"


    @classmethod
    def get_active_sync(cls):
        """
        Возвращает активный объект BotSettings ИЛИ словарь с дефолтными значениями.
        """
        obj = cls.objects.filter(active=True).first()
        if obj:
            return obj

        # Если нет активных настроек — возвращаем словарь-заглушку
        return {
            "active": False,
            "picture_chat_id": "-1001890980411",
            "parser_url_ozon": "",
            "parser_url_wb": "",
            "marketing_group_id": "",
            "created_at": None,
            "updated_at": None,
        }

    @classmethod
    async def get_active(cls):
        """
        Асинхронный метод: возвращает активную запись.
        Использует sync_to_async внутри.
        """
        return await sync_to_async(cls.get_active_sync)()