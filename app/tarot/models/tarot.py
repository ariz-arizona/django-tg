from django.db import models
from django.contrib.contenttypes.fields import GenericRelation
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.fields import ArrayField

from tg_bot.models import BotFile, BotFileMixin
from .base import bot_prefix, ActiveDeckManager


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
    slug = models.SlugField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        verbose_name="URL-идентификатор",
        help_text="Человекопонятный URL для колоды",
    )
    link = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        default=None,
        verbose_name="Ссылка на колоду",
    )
    seo_tags = ArrayField(
        models.CharField(max_length=255),
        null=True,
        blank=True,
        default=list,
        verbose_name="SEO-теги",
        help_text="Список тегов для поиска (например, ['уэйт', 'waite', 'rider'])",
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Активна",
        help_text="Если выключено, колода не участвует в поиске и раскладах"
    )
    
    objects = ActiveDeckManager()  # По умолчанию только активные
    all_decks = models.Manager()   # Вообще все, включая неактивные

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = f"{bot_prefix}: Колода"
        verbose_name_plural = f"{bot_prefix}: Колоды"
        indexes = [
        ]
        

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