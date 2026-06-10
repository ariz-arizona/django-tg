from django.db import models

from django.utils import timezone

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.fields import GenericRelation

from tg_bot.models import TgUser, Bot
from server.logger import logger

bot_prefix = "Tarot"


class TarotCard(models.Model):
    card_id = models.CharField(max_length=10, unique=True, verbose_name="ID карты")
    name = models.CharField(max_length=255, verbose_name="Название карты")
    meaning = models.TextField(verbose_name="Основное значение")
    meaning_url = models.URLField(
        verbose_name="Ссылка на значение", blank=True, null=True
    )
    is_major = models.BooleanField(default=False)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = f"{bot_prefix}: Карта Таро"
        verbose_name_plural = f"{bot_prefix}: Карты Таро"


class TarotMeaningCategory(models.Model):
    name = models.CharField(max_length=50, verbose_name="Категория", unique=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = f"{bot_prefix}: Категория"
        verbose_name_plural = f"{bot_prefix}: Категории"


class ExtendedMeaning(models.Model):
    tarot_card = models.ForeignKey(
        TarotCard, on_delete=models.CASCADE, related_name="extended_meanings"
    )
    category = models.CharField(max_length=50, verbose_name="Категория")
    category_base = models.ForeignKey(
        TarotMeaningCategory, on_delete=models.SET_NULL, null=True, related_name="cat"
    )
    text = models.TextField(verbose_name="Текст значения")

    def __str__(self):
        return f"{self.tarot_card.name}"

    class Meta:
        verbose_name = f"{bot_prefix}: Расширенное толкование"
        verbose_name_plural = f"{bot_prefix}: Расширенные толкования"


class TarotDeck(models.Model):
    """
    Модель для хранения информации о колоде Таро.
    """

    name = models.CharField(max_length=255, unique=True, verbose_name="Название колоды")
    link = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        default=None,
        verbose_name="Ссылка на колоду",
    )

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = f"{bot_prefix}: Колода"
        verbose_name_plural = f"{bot_prefix}: Колоды"


class BotFile(models.Model):
    # Поля для Generic Relation
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")

    bot = models.ForeignKey("tg_bot.Bot", on_delete=models.CASCADE, verbose_name="Бот")
    file_id = models.CharField(max_length=255, verbose_name="Telegram File ID")

    class Meta:
        verbose_name = "TG file ID для ботов"
        verbose_name_plural = "TG file IDs для ботов"
        unique_together = ("bot", "file_id")


class BotFileMixin:
    """Миксин для асинхронного получения файла бота"""

    async def aget_file_id(
        self,
        bot_id,
        default="https://upload.wikimedia.org/wikipedia/commons/thumb/3/3a/Cat03.jpg/960px-Cat03.jpg",
    ):
        # self.files — это GenericRelation, работает как менеджер
        file_obj = await self.files.filter(bot_id=bot_id).afirst()
        return file_obj.file_id if file_obj else default


class BotFileCache(models.Model):
    """
    Модель для кэширования временных путей скачивания по file_id.
    """

    bot_file = models.OneToOneField(
        BotFile,
        on_delete=models.CASCADE,
        related_name="file_cache",
        verbose_name="Bot файл",
    )

    # Временный путь для скачивания (относительный путь от API Telegram)
    file_path = models.CharField(max_length=512, verbose_name="Временный file_path")

    # Время истечения ссылки (обычно 1 час, ставим запас)
    expires_at = models.DateTimeField(
        verbose_name="Истекает в",
        default=lambda: timezone.now() + timezone.timedelta(minutes=10),
    )

    def is_expired(self):
        # Добавляем 5-минутный буфер, чтобы не попасть на истекшую ссылку в процессе скачивания
        return timezone.now() >= (self.expires_at - timezone.timedelta(minutes=5))

    def get_cache_link(self):
        """
        Получает прямую ссылку на файл через Telegram Bot API.
        Сначала проверяет, не истекла ли текущая ссылка.
        Если истекла - обновляет кэш и возвращает новую ссылку.
        """
        import requests
        from django.utils import timezone as django_timezone

        # Проверяем, не протухла ли текущая ссылка
        if not self.is_expired() and self.file_path:
            # Если не протухла - возвращаем существующую ссылку
            bot_token = self.bot_file.bot.token
            return f"https://api.telegram.org/file/bot{bot_token}/{self.file_path}"

        # Если протухла - получаем новую
        bot_token = self.bot_file.bot.token
        url = f"https://api.telegram.org/bot{bot_token}/getFile"

        response = requests.post(url, data={"file_id": self.bot_file.file_id})

        if response.status_code == 200:
            data = response.json()
            if data.get("ok"):
                # Обновляем кэш
                self.file_path = data["result"]["file_path"]
                # Обновляем время истечения (обычно через 1 час)
                self.expires_at = django_timezone.now() + django_timezone.timedelta(
                    hours=1
                )
                self.save()
                
                # Возвращаем новую ссылку
                return f"https://api.telegram.org/file/bot{bot_token}/{self.file_path}"
            else:
                logger.error(f"Ошибка Telegram API при получении file_path: {data}")
        else:
            logger.error(f"Ошибка HTTP {response.status_code} при запросе к Telegram API: {response.text}")

        # Если произошла ошибка, возвращаем None
        logger.error(f"Не удалось получить ссылку для bot_file_id={self.bot_file.id}, file_id={self.bot_file.file_id}")
        return None
    
    @classmethod
    async def acreate_and_get_link(cls, bot_file, **kwargs):
        """Возвращает только ссылку"""
        from django.core.exceptions import ObjectDoesNotExist
        from django.db import IntegrityError
        
        try:
            cache = await cls.objects.aget(bot_file=bot_file)
            logger.info('get')
            logger.info(cache.__dict__)
            return await cache.aget_cache_link()
        except ObjectDoesNotExist:
            try:
                cache = cls(bot_file=bot_file, **kwargs)
                logger.info('create')
                logger.info(cache.__dict__)
                return await cache.aget_cache_link()
            except IntegrityError:
                try:
                    cache = await cls.objects.aget(bot_file=bot_file)
                    return await cache.aget_cache_link()
                except ObjectDoesNotExist:
                    return None

    async def aget_cache_link(self):
        """
        Асинхронно получает прямую ссылку на файл через Telegram Bot API.
        Сначала проверяет, не истекла ли текущая ссылка.
        Если истекла - обновляет кэш и возвращает новую ссылку.
        """
        import aiohttp

        bot_file_instance = await BotFile.objects.aget(id=self.bot_file_id)
        bot_instance = await Bot.objects.aget(id=bot_file_instance.bot_id)
        bot_token = bot_instance.token
        
        # Проверяем, не протухла ли текущая ссылка
        if not self.is_expired() and self.file_path:
            # Если не протухла - возвращаем существующую ссылку
            return f"https://api.telegram.org/file/bot{bot_token}/{self.file_path}"

        # Если протухла - получаем новую
        url = f"https://api.telegram.org/bot{bot_token}/getFile"

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, data={"file_id": bot_file_instance.file_id}
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("ok"):
                        # Обновляем кэш
                        self.file_path = data["result"]["file_path"]
                        # Обновляем время истечения (обычно через 1 час)
                        self.expires_at = timezone.now() + timezone.timedelta(hours=1)
                        await self.asave()  # Для Django 3.1+ с поддержкой асинхронного save

                        # Возвращаем новую ссылку
                        return f"https://api.telegram.org/file/bot{bot_token}/{self.file_path}"

        return None

    def __str__(self):
        return f"Cache for {self.file_path[:10]}"

    class Meta:
        verbose_name = f"{bot_prefix}: Кэш путей файлов"
        verbose_name_plural = f"{bot_prefix}: Кэши путей файлов"


class TarotCardItem(models.Model, BotFileMixin):
    """
    Модель для хранения информации о карте в колоде.
    """

    deck = models.ForeignKey(
        TarotDeck, on_delete=models.CASCADE, related_name="cards", verbose_name="Колода"
    )
    tarot_card = models.ForeignKey(
        "TarotCard",
        on_delete=models.CASCADE,
        related_name="deck_cards",
        verbose_name="Карта Таро",
    )
    files = GenericRelation(BotFile, related_query_name="tarot_cards")

    def __str__(self):
        return f"{self.tarot_card.name} в колоде {self.deck.name}"

    class Meta:
        verbose_name = f"{bot_prefix}: Карта в колоде"
        verbose_name_plural = f"{bot_prefix}: Карты в колодах"
        unique_together = ("deck", "tarot_card")


class TarotUserReading(models.Model):
    user = models.ForeignKey(
        "tg_bot.TgUser",  # Укажите имя модели, если TgUser определена в другом месте
        on_delete=models.SET_NULL,
        related_name="readings",
        null=True,
        verbose_name="Пользователь",  # Человекочитаемое имя для поля
    )
    date = models.DateTimeField(
        default=timezone.now, verbose_name="Дата гадания"
    )  # Дата и время гадания
    text = models.TextField(blank=True, verbose_name="Текст гадания")  # Текст гадания
    message_id = models.IntegerField(
        null=True, blank=True, verbose_name="ID сообщения"
    )  # Идентификатор сообщения в Telegram

    def __str__(self):
        username = self.user.username if self.user else "Unknown User"
        return f"Reading by {username} on {self.date}"

    class Meta:
        verbose_name = f"{bot_prefix}: Пользовательское гадание"
        verbose_name_plural = f"{bot_prefix}: Пользовательские гадания"
        indexes = [
            models.Index(fields=["user", "date"]),
        ]


class OraculumDeck(models.Model):
    name = models.CharField(
        max_length=255,
        verbose_name="Название колоды",
        help_text="Название колоды (например, 'Колода МЛАДЕНЦА').",
    )
    description = models.TextField(
        verbose_name="Описание колоды",
        help_text="Краткое описание колоды.",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Дата создания",
        help_text="Дата и время создания колоды.",
    )

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = f"{bot_prefix}: Колода оракула"
        verbose_name_plural = f"{bot_prefix}: Колоды оракула"


class OraculumItem(models.Model, BotFileMixin):
    deck = models.ForeignKey(
        OraculumDeck,
        on_delete=models.CASCADE,
        related_name="cards",
        verbose_name="Колода",
        help_text="Колода, к которой относится карта.",
    )
    files = GenericRelation(BotFile, related_query_name="oraculum_cards")
    name = models.CharField(
        max_length=255,
        verbose_name="Название карты",
        help_text="Название карты (например, 'МЛАДЕНЕЦ').",
    )
    description = models.TextField(
        blank=True,
        null=True,
        verbose_name="Описание карты",
        help_text="Краткое описание карты.",
    )
    direct = models.TextField(
        blank=True,
        null=True,
        verbose_name="Прямое значение",
        help_text="Значение карты в прямом положении.",
    )
    inverted = models.TextField(
        blank=True,
        null=True,
        verbose_name="Перевернутое значение",
        help_text="Значение карты в перевернутом положении.",
    )

    def __str__(self):
        return f"{self.name} (из колоды: {self.deck.name})"

    class Meta:
        verbose_name = f"{bot_prefix}: Карта оракула"
        verbose_name_plural = f"{bot_prefix}: Карты оракула"


class Rune(models.Model):
    type = models.CharField(max_length=50, verbose_name="Тип руны")
    symbol = models.CharField(max_length=10, verbose_name="Символ руны")
    sticker = models.CharField(max_length=100, verbose_name="ID стикера")

    # Прямое значение
    straight_keys = models.TextField(verbose_name="Ключи (прямое)")
    straight_meaning = models.TextField(verbose_name="Значение (прямое)")
    straight_pos_1 = models.TextField(verbose_name="Позиция 1 (прямое)")
    straight_pos_2 = models.TextField(verbose_name="Позиция 2 (прямое)")
    straight_pos_3 = models.TextField(verbose_name="Позиция 3 (прямое)")

    # Перевернутое значение
    inverted_keys = models.TextField(
        blank=True, null=True, verbose_name="Ключи (перевернутое)"
    )
    inverted_meaning = models.TextField(
        blank=True, null=True, verbose_name="Значение (перевернутое)"
    )
    inverted_pos_1 = models.TextField(
        blank=True, null=True, verbose_name="Позиция 1 (перевернутое)"
    )
    inverted_pos_2 = models.TextField(
        blank=True, null=True, verbose_name="Позиция 2 (перевернутое)"
    )
    inverted_pos_3 = models.TextField(
        blank=True, null=True, verbose_name="Позиция 3 (перевернутое)"
    )

    def __str__(self):
        return f"{self.symbol} ({self.type})"

    class Meta:
        verbose_name = f"{bot_prefix}: Руна"
        verbose_name_plural = f"{bot_prefix}: Руны"
