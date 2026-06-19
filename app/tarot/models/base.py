from django.db import models
bot_prefix = "Tarot"


class ActiveDeckManager(models.Manager):
    """Менеджер, возвращающий только активные колоды"""
    def get_queryset(self):
        return super().get_queryset().filter(is_active=True)