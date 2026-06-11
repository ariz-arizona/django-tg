# roster/models.py — добавь после Season
from django.db import models

from tg_bot.models import Bot

class RollLimit(models.Model):
    LIMIT_TYPE_CHOICES = [
        ("cooldown", "Кулдаун (сек)"),
        ("daily", "Дневной лимит"),
        ("bihourly", "Двухчасовой лимит"),
    ]

    bot = models.ForeignKey(
        'tg_bot.Bot',
        on_delete=models.CASCADE,
        related_name='roll_limits',
        verbose_name='Бот'
    )
    limit_type = models.CharField(
        max_length=20,
        choices=LIMIT_TYPE_CHOICES,
        verbose_name='Тип лимита'
    )
    value = models.PositiveIntegerField(
        verbose_name='Значение',
        help_text='Для кулдауна — секунды, для дневного/двухчасового — кол-во попыток'
    )

    class Meta:
        unique_together = ['bot', 'limit_type']
        verbose_name = 'Лимит бросков'
        verbose_name_plural = 'Лимиты бросков'

    def __str__(self):
        return f"{self.bot.name} — {self.get_limit_type_display()}: {self.value}"
    
class BotText(models.Model):
    """Текстовые значения для бота, настраиваемые через админку."""

    class TextType(models.TextChoices):
        ROLL = "roll", "Текст броска"
        START = "start", "Приветственное сообщение"
        CAPTION = "caption", "Подпись к карточке"

    bot = models.ForeignKey(
        Bot,
        on_delete=models.CASCADE,
        related_name='texts',
        verbose_name='Бот'
    )
    text_type = models.CharField(
        max_length=20,
        choices=TextType.choices,
        verbose_name='Тип текста'
    )
    text = models.TextField(
        verbose_name='Текст',
        help_text='Можно использовать HTML-теги и переменные в фигурных скобках'
    )

    class Meta:
        verbose_name = 'Текст бота'
        verbose_name_plural = 'Тексты ботов'
        unique_together = [('bot', 'text_type')]

    def __str__(self):
        return f'{self.bot} — {self.get_text_type_display()}'