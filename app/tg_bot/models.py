from django.db import models


class Bot(models.Model):
    name = models.CharField(max_length=100, verbose_name="Название бота")
    token = models.CharField(max_length=255, verbose_name="Токен")
    chat_id = models.CharField(max_length=50, verbose_name="Chat ID")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Дата обновления")

    def __str__(self):
        return self.name
