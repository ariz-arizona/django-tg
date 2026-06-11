# roster/models.py — добавь в конец файла
from django.db import models

from roster.models.team import Card, Season

class UserRoll(models.Model):
    user = models.ForeignKey(
        'tg_bot.TgUser',
        on_delete=models.CASCADE,
        related_name='rolls',
        verbose_name='Пользователь'
    )
    bot = models.ForeignKey(
        'tg_bot.Bot',
        on_delete=models.CASCADE,
        related_name='rolls',
        verbose_name='Бот'
    )
    card = models.ForeignKey(
        Card,
        on_delete=models.CASCADE,
        related_name='rolls',
        verbose_name='Карта'
    )
    season = models.ForeignKey(
        Season,
        on_delete=models.CASCADE,
        related_name='rolls',
        verbose_name='Сезон'
    )
    rolled_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Время ролла'
    )

    class Meta:
        ordering = ['-rolled_at']
        verbose_name = 'Ролл пользователя'
        verbose_name_plural = 'Роллы пользователей'
        indexes = [
            models.Index(fields=['user', 'season']),
            models.Index(fields=['user', 'bot', 'rolled_at']),
        ]

    def __str__(self):
        return f"{self.user} → {self.card} ({self.rolled_at:%d.%m.%Y %H:%M})"