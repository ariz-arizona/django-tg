from django.db import models
from .base import bot_prefix


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