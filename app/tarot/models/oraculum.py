from django.db import models
from django.contrib.contenttypes.fields import GenericRelation
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.fields import ArrayField

from tg_bot.models import BotFile, BotFileMixin
from .base import bot_prefix, ActiveDeckManager


class OraculumDeck(models.Model):
    name = models.CharField(
        max_length=255,
        verbose_name="Название колоды",
        help_text="Название колоды (например, 'Колода МЛАДЕНЦА').",
    )
    slug = models.SlugField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        verbose_name="URL-идентификатор",
        help_text="Человекопонятный URL для колоды",
    )
    description = models.TextField(
        verbose_name="Описание колоды",
        help_text="Краткое описание колоды.",
        blank=True,
        null=True,
    )
    seo_tags = ArrayField(
        models.CharField(max_length=255),
        null=True,
        blank=True,
        default=list,
        verbose_name="SEO-теги",
        help_text="Список тегов для поиска (например, ['уэйт', 'waite', 'rider'])",
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Дата создания",
        help_text="Дата и время создания колоды.",
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
        verbose_name = f"{bot_prefix}: Колода оракула"
        verbose_name_plural = f"{bot_prefix}: Колоды оракула"
        indexes = [
            GinIndex(
                fields=['slug'], 
                name='oraculumdeck_slug_trgm_idx', 
                opclasses=['gin_trgm_ops']
            ),
        ]


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
    
    @property
    def display_name(self):
        """Возвращает custom_name, если он задан, иначе стандартное название карты."""
        return self.name

    @property
    def display_description(self):
        """Возвращает custom_description, если он задан, иначе стандартное описание карты."""
        return self.description

    def __str__(self):
        return f"{self.name} (из колоды: {self.deck.name})"

    class Meta:
        verbose_name = f"{bot_prefix}: Карта оракула"
        verbose_name_plural = f"{bot_prefix}: Карты оракула"