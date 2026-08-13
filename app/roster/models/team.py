# roster/models.py
from django.db import models
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.contrib.contenttypes.fields import GenericRelation
from django.utils.text import slugify

from tg_bot.models import Bot, BotFile, BotFileMixin


class SeasonQuerySet(models.QuerySet):
    def active(self):
        now = timezone.now()
        return self.filter(
            is_archived=False,
            start_date__lte=now,
        ).filter(
            models.Q(end_date__isnull=True) | models.Q(end_date__gte=now)
        )

    def upcoming(self):
        return self.filter(
            is_archived=False,
            start_date__gt=timezone.now(),
        )

    def archived(self):
        return self.filter(is_archived=True)


class Season(models.Model):
    name = models.CharField(max_length=100, verbose_name='Название')
    slug = models.SlugField(
        max_length=100,
        unique=True,
        blank=True,
        verbose_name='Slug'
    )
    start_date = models.DateTimeField(verbose_name='Начало')
    end_date = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Окончание'
    )
    is_archived = models.BooleanField(
        default=False,
        verbose_name='В архиве'
    )
    bot = models.ForeignKey(
        Bot,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='seasons',
        verbose_name='Бот'
    )

    objects = SeasonQuerySet.as_manager()

    class Meta:
        ordering = ['-start_date']
        verbose_name = 'Сезон'
        verbose_name_plural = 'Сезоны'

    @property
    def is_active(self):
        now = timezone.now()
        if self.is_archived:
            return False
        if self.start_date > now:
            return False
        if self.end_date and self.end_date < now:
            return False
        return True

    @property
    def is_perpetual(self):
        return self.end_date is None

    @property
    def status(self):
        if self.is_archived:
            return "в архиве"
        if self.start_date > timezone.now():
            return "скоро"
        if self.is_active:
            return "активен"
        return "завершён"

    def clean(self):
        if self.end_date and self.end_date < self.start_date:
            raise ValidationError({
                'end_date': 'Дата окончания не может быть раньше даты начала.'
            })

    def __str__(self):
        end = "∞" if self.is_perpetual else self.end_date.strftime("%d.%m.%Y")
        return f"{self.name} ({self.status}: {self.start_date:%d.%m.%Y} – {end})"
    
    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.name)
            slug = base_slug
            counter = 1
            while Season.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)


class Team(models.Model, BotFileMixin):
    season = models.ForeignKey(
        Season,
        on_delete=models.CASCADE,
        related_name='teams',
        verbose_name='Сезон'
    )
    name = models.CharField(max_length=255, verbose_name='Название')
    stars = models.PositiveSmallIntegerField(
        default=1,
        verbose_name='Звёздность'
    )
    files = GenericRelation(BotFile, verbose_name='Файлы команды')

    class Meta:
        ordering = ['-stars', 'name']
        verbose_name = 'Команда'
        verbose_name_plural = 'Команды'

    def __str__(self):
        return f"{'⭐' * self.stars} {self.name}"


class Tag(models.Model):
    name = models.CharField(max_length=255, unique=True, verbose_name='Тег')

    class Meta:
        ordering = ['name']
        verbose_name = 'Тег'
        verbose_name_plural = 'Теги'

    def __str__(self):
        return self.name


class Card(models.Model, BotFileMixin):
    season = models.ForeignKey(
        Season,
        on_delete=models.CASCADE,
        related_name='cards',
        verbose_name='Сезон'
    )
    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='cards',
        verbose_name='Команда'
    )
    name = models.CharField(max_length=255, verbose_name='Название')
    stars = models.PositiveSmallIntegerField(
        default=1,
        verbose_name='Звёздность'
    )
    description = models.TextField(blank=True, verbose_name='Описание')
    tags = models.ManyToManyField(
        Tag,
        blank=True,
        related_name='cards',
        verbose_name='Теги'
    )
    image = GenericRelation(
        BotFile,
        related_query_name='card_image',
        verbose_name='Открытая картинка'
    )

    class Meta:
        ordering = ['-stars', 'name']
        verbose_name = 'Карта'
        verbose_name_plural = 'Карты'

    def clean(self):
        if self.team and self.team.season_id != self.season_id:
            raise ValidationError({
                'team': 'Команда должна принадлежать тому же сезону, что и карточка.'
            })

    def __str__(self):
        team_part = f" — {self.team.name}" if self.team else ""
        return f"{'⭐' * self.stars} {self.name}{team_part} ({self.season.name})"

    async def aget_image_id(self, bot_id):
        return await self.aget_file_id(bot_id, field_name="image")

    async def aget_image_hidden_id(self, bot_id):
        return await self.aget_file_id(bot_id, field_name="image_hidden")