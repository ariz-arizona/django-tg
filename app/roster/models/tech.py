# roster/models.py — добавь после Season
import re
import math

from django.db import models
from django.db.models import Q, UniqueConstraint
from django.core.exceptions import ValidationError

from tg_bot.models import Bot

class RollLimit(models.Model):
    LIMIT_TYPE_CHOICES = [
        ("cooldown", "Кулдаун (сек)"),
        ("daily", "Дневной лимит"),
        ("bihourly", "Двухчасовой лимит"),
        ("craft", "обмен карт"),
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
    is_premium = models.BooleanField(
        default=False,
        verbose_name='Премиум лимит',
        help_text='Применить этот лимит только к премиум-пользователям'
    )

    class Meta:
        # Теперь уникальность проверяется с учетом флага премиума
        unique_together = ['bot', 'limit_type', 'is_premium']
        verbose_name = 'Лимит бросков'
        verbose_name_plural = 'Лимиты бросков'

    def __str__(self):
        premium_status = " [Premium]" if self.is_premium else ""
        return f"{self.bot.name} — {self.get_limit_type_display()}{premium_status}: {self.value}"
        
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
    
    
class RarityWeight(models.Model):
    """
    Модель для расчета весов редкости предметов
    
    Базовая формула веса: 1 / (star! * (star + 1))
    где star - уровень редкости от 1 до 5
    """
    enabled = models.BooleanField(
        default=True,
        verbose_name="Активно",
        help_text="Включить/выключить данный расчет весов"
    )
    
    formula = models.TextField(
        default="1 / (math.factorial({star}) * ({star} + 1))",
        verbose_name="Формула веса",
        help_text="Формула расчета веса. Используйте {star} для подстановки уровня редкости (1-5). "
                  "Доступны функции: math.factorial, math.pow, math.sqrt, abs, round, min, max"
    )
    
    coefficient = models.FloatField(
        default=1.0,
        verbose_name="Коэффициент",
        help_text="Множитель для корректировки весов"
    )
    
    bot = models.ForeignKey(
        'tg_bot.Bot',
        on_delete=models.CASCADE,
        related_name='rarity_weights',
        verbose_name="Бот"
    )
    
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")
    
    class Meta:
        verbose_name = "Вес редкости"
        verbose_name_plural = "Веса редкостей"
        constraints = [
            UniqueConstraint(
                fields=['bot'],
                condition=Q(enabled=True),
                name='unique_enabled_per_bot'
            )
        ]
        indexes = [
            models.Index(fields=['bot', 'enabled']),
        ]
    
    def __str__(self):
        status = "✓" if self.enabled else "✗"
        return f"{status} Расчет весов для {self.bot} (коэф: {self.coefficient})"
    
    def clean(self):
        """Валидация формулы и проверка результата"""
        super().clean()
        
        if self.formula:
            self._validate_formula_result()
    
    def _validate_formula_result(self):
        """Проверка, что результат вычисления > 0 для всех уровней редкости"""
        safe_dict = {
            '__builtins__': {},
            'math': math,
            'abs': abs,
            'round': round,
            'min': min,
            'max': max,
        }
        
        for test_star in range(1, 6):
            try:
                test_formula = self.formula.replace('{star}', str(test_star))
                result = eval(test_formula, {"__builtins__": {}}, safe_dict)
                
                if not isinstance(result, (int, float)):
                    raise ValidationError({
                        'formula': f'Формула должна возвращать число. Для ★{test_star} получен тип: {type(result).__name__}'
                    })
                
                if result <= 0:
                    raise ValidationError({
                        'formula': f'Вес для ★{test_star} должен быть > 0. Получено: {result}'
                    })
                
                if math.isinf(result) or math.isnan(result):
                    raise ValidationError({
                        'formula': f'Некорректный результат для ★{test_star}: {result}'
                    })
                    
            except ZeroDivisionError:
                raise ValidationError({
                    'formula': f'Деление на ноль при расчете веса для ★{test_star}'
                })
            except (SyntaxError, NameError, TypeError) as e:
                raise ValidationError({
                    'formula': f'Ошибка в формуле для ★{test_star}: {str(e)}'
                })
            except ValidationError:
                raise
            except Exception as e:
                raise ValidationError({
                    'formula': f'Ошибка вычисления для ★{test_star}: {str(e)}'
                })
    
    def calculate_weight(self, star):
        """
        Вычисление веса для конкретного уровня редкости
        
        Args:
            star: уровень редкости (1-5)
            
        Returns:
            float: вычисленный вес с учетом коэффициента
        """
        if not 1 <= star <= 5:
            raise ValueError("Уровень редкости должен быть от 1 до 5")
        
        safe_dict = {
            '__builtins__': {},
            'math': math,
            'abs': abs,
            'round': round,
            'min': min,
            'max': max,
        }
        
        formula = self.formula.replace('{star}', str(star))
        
        try:
            result = eval(formula, {"__builtins__": {}}, safe_dict)
            return float(result) * self.coefficient
        except Exception as e:
            raise ValueError(f"Ошибка вычисления веса: {str(e)}")
    
    def get_all_weights(self):
        """
        Получить все веса для редкостей 1-5
        
        Returns:
            dict: {star: weight}
        """
        weights = {}
        for star in range(1, 6):
            weights[star] = self.calculate_weight(star)
        return weights
    
    def get_probabilities(self):
        """
        Получить вероятности в процентах для всех редкостей
        
        Returns:
            dict: {star: probability_percent}
        """
        weights = self.get_all_weights()
        total = sum(weights.values())
        
        if total == 0:
            return {star: 0 for star in range(1, 6)}
        
        return {star: (weight / total) * 100 for star, weight in weights.items()}
    
    @staticmethod
    def get_default_weights():
        """Статический метод для получения весов по умолчанию"""
        weights = {}
        for star in range(1, 6):
            weight = 1 / (math.factorial(star) * (star + 1))
            weights[star] = weight
        return weights
    
    def save(self, *args, **kwargs):
        """Сохраняем с полной валидацией"""
        self.full_clean()
        super().save(*args, **kwargs)