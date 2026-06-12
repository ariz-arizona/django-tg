# roster/models.py — добавь в конец файла
from django.db import models

from roster.models.team import Card, Season

class RosterUser(models.Model):
    user = models.OneToOneField(
        'tg_bot.TgUser',
        on_delete=models.CASCADE,
        related_name='gacha_profile',
        verbose_name='Пользователь Telegram'
    )
    is_premium = models.BooleanField(
        default=False,
        verbose_name='Премиум статус',
        help_text='Дает доступ к повышенным лимитам и фичам гачи'
    )
    description = models.TextField(
        blank=True,
        null=True,
        verbose_name='Описание/Статус',
        help_text='Кастомное описание профиля или фандомный статус игрока'
    )

    class Meta:
        verbose_name = 'Профиль игрока'
        verbose_name_plural = 'Профили игроков'

    def __str__(self):
        return f"Гача-профиль: {self.user.username or self.user.tg_id}"
    
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
    is_used_for_craft = models.BooleanField(
        default=False,
        verbose_name='Использовано для крафта',
        help_text='Если True, карта была сожжена в рецепте крафта и больше не принадлежит игроку'
    )

    class Meta:
        ordering = ['-rolled_at']
        verbose_name = 'Ролл пользователя'
        verbose_name_plural = 'Роллы пользователей'
        indexes = [
            # Обновили индекс, так как теперь фильтрация всегда будет включать крафт-флаг
            models.Index(fields=['user', 'season', 'is_used_for_craft']),
            models.Index(fields=['user', 'bot', 'rolled_at']),
        ]

    def __str__(self):
        return f"{self.user} → {self.card} ({self.rolled_at:%d.%m.%Y %H:%M})"