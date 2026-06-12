from django.db import models

from django.utils import timezone

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.fields import GenericRelation

from tg_bot.models import TgUser, BotFile, BotFileMixin
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

class UserReading(models.Model):
    # Основной инструмент гадания
    class ReadingCategory(models.TextChoices):
        ONE = "one", "Одна карта)"  # Для команды /one
        TAROT = "tarot", "Таро"                        # Для /card, /card3
        ORACLE = "oracle", "Оракул"                    # Для /oraculum, /oraculum3
        RUNES = "runes", "Руны (Футарк)"               # Для /futark, /futark triplet
        CANVAS_SPREAD = "canvas_spread", "Расклад на холсте" # Для /spread

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

    created_at = models.DateTimeField(
        auto_now_add=True, 
        verbose_name="Дата гадания"  # Оставляем понятное имя для админки
    )
    updated_at = models.DateTimeField(
        auto_now=True, 
        verbose_name="Дата изменения"
    )
    
    # Основные данные
    text = models.TextField(blank=True, verbose_name="Текст гадания")
    message_id = models.IntegerField(null=True, blank=True, verbose_name="ID сообщения")

    def __str__(self):
        username = self.user.username if self.user else "Unknown User"
        return f"{self.get_category_display()} - {username}"

    class Meta:
        verbose_name = f"{bot_prefix}: Пользовательское гадание"
        verbose_name_plural = f"{bot_prefix}: Пользовательские гадания"
        
        # Индексы для быстрой фильтрации по категориям и датам
        indexes = [
            models.Index(fields=["user", "category", "created_at"]),
        ]