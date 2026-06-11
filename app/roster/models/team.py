# roster/models.py
from django.db import models
from django.contrib.contenttypes.fields import GenericRelation

from tg_bot.models import Bot, BotFile, BotFileMixin

class Season(models.Model):
    name = models.CharField(max_length=100)
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    bot = models.ForeignKey(
        Bot,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='seasons',
        verbose_name='Бот'
    )

    class Meta:
        ordering = ['-start_date']
        verbose_name = 'Сезон'
        verbose_name_plural = 'Сезоны'

    def __str__(self):
        return f"{self.name} ({'Активен' if self.is_active else 'Завершён'})"


class Team(models.Model, BotFileMixin):
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
    files = GenericRelation(BotFile, verbose_name='Файлы команды')  
    
    class Meta:
        ordering = ['-stars', 'name']
        verbose_name = 'Команда'
        verbose_name_plural = 'Команды'

    def __str__(self):
        return f"{'⭐' * self.stars} {self.name}"


class Card(models.Model, BotFileMixin):
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
    image = GenericRelation(
        BotFile,
        related_query_name='card_image',
        verbose_name='Открытая картинка'
    )

    async def aget_image_id(self, bot_id):
        return await self.aget_file_id(bot_id, field_name="image")

    async def aget_image_hidden_id(self, bot_id):
        return await self.aget_file_id(bot_id, field_name="image_hidden")

    class Meta:
        ordering = ['-stars', 'name']
        verbose_name = 'Карта'
        verbose_name_plural = 'Карты'

    def __str__(self):
        return f"{'⭐' * self.stars} {self.name} — {self.team.name}"