# roster/models.py
from django.db import models


class Season(models.Model):
    name = models.CharField(max_length=100)
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-start_date']
        verbose_name = 'Сезон'
        verbose_name_plural = 'Сезоны'

    def __str__(self):
        return f"{self.name} ({'Активен' if self.is_active else 'Завершён'})"


class Team(models.Model):
    season = models.ForeignKey(
        Season,
        on_delete=models.CASCADE,
        related_name='teams',
        verbose_name='Сезон'
    )
    name = models.CharField(max_length=100, verbose_name='Название')
    stars = models.PositiveSmallIntegerField(
        default=1,
        verbose_name='Звёздность'
    )

    class Meta:
        ordering = ['-stars', 'name']
        verbose_name = 'Команда'
        verbose_name_plural = 'Команды'

    def __str__(self):
        return f"{'⭐' * self.stars} {self.name}"


class Card(models.Model):
    team = models.ForeignKey(
        Team,
        on_delete=models.CASCADE,
        related_name='cards',
        verbose_name='Команда'
    )
    name = models.CharField(max_length=100, verbose_name='Название')
    stars = models.PositiveSmallIntegerField(
        default=1,
        verbose_name='Звёздность'
    )
    description = models.TextField(blank=True, verbose_name='Описание')
    image = models.CharField(
        max_length=255,
        blank=True,
        verbose_name='Картинка'
    )

    class Meta:
        ordering = ['-stars', 'name']
        verbose_name = 'Карта'
        verbose_name_plural = 'Карты'

    def __str__(self):
        return f"{'⭐' * self.stars} {self.name} — {self.team.name}"