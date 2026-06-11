from django.db import models

from django.utils import timezone

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.fields import GenericRelation

from tg_bot.models import TgUser, Bot
from server.logger import logger

bot_prefix = "BOT FILE"

class BotFile(models.Model):
    # Поля для Generic Relation
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")

    bot = models.ForeignKey("tg_bot.Bot", on_delete=models.CASCADE, verbose_name="Бот")
    file_id = models.CharField(max_length=255, verbose_name="Telegram File ID")

    class Meta:
        verbose_name = f"{bot_prefix}: Telegram File ID"
        verbose_name_plural = f"{bot_prefix}: Telegram File IDs"
        unique_together = ("bot", "file_id")


class BotFileMixin:
    """Миксин для асинхронного получения файла бота"""

    async def aget_file_id(
        self,
        bot_id,
        default="https://upload.wikimedia.org/wikipedia/commons/thumb/3/3a/Cat03.jpg/960px-Cat03.jpg",
    ):
        # self.files — это GenericRelation, работает как менеджер
        file_obj = await self.files.filter(bot_id=bot_id).afirst()
        return file_obj.file_id if file_obj else default

def get_default_expires_at():
    """Возвращает время истечения по умолчанию (через 10 минут)."""
    return timezone.now() + timezone.timedelta(minutes=10)

class BotFileCache(models.Model):
    """
    Модель для кэширования временных путей скачивания по file_id.
    """

    bot_file = models.OneToOneField(
        BotFile,
        on_delete=models.CASCADE,
        related_name="file_cache",
        verbose_name="Bot файл",
    )

    # Временный путь для скачивания (относительный путь от API Telegram)
    file_path = models.CharField(max_length=512, verbose_name="Временный file_path")

    # Время истечения ссылки (обычно 1 час, ставим запас)
    expires_at = models.DateTimeField(
        verbose_name="Истекает в",
        default=get_default_expires_at,
    )

    def is_expired(self):
        # Добавляем 5-минутный буфер, чтобы не попасть на истекшую ссылку в процессе скачивания
        return timezone.now() >= (self.expires_at - timezone.timedelta(minutes=5))

    def get_cache_link(self):
        """
        Получает прямую ссылку на файл через Telegram Bot API.
        Сначала проверяет, не истекла ли текущая ссылка.
        Если истекла - обновляет кэш и возвращает новую ссылку.
        """
        import requests
        from django.utils import timezone as django_timezone

        # Проверяем, не протухла ли текущая ссылка
        if not self.is_expired() and self.file_path:
            # Если не протухла - возвращаем существующую ссылку
            bot_token = self.bot_file.bot.token
            return f"https://api.telegram.org/file/bot{bot_token}/{self.file_path}"

        # Если протухла - получаем новую
        bot_token = self.bot_file.bot.token
        url = f"https://api.telegram.org/bot{bot_token}/getFile"

        response = requests.post(url, data={"file_id": self.bot_file.file_id})

        if response.status_code == 200:
            data = response.json()
            if data.get("ok"):
                # Обновляем кэш
                self.file_path = data["result"]["file_path"]
                # Обновляем время истечения (обычно через 1 час)
                self.expires_at = django_timezone.now() + django_timezone.timedelta(
                    hours=1
                )
                self.save()
                
                # Возвращаем новую ссылку
                return f"https://api.telegram.org/file/bot{bot_token}/{self.file_path}"
            else:
                logger.error(f"Ошибка Telegram API при получении file_path: {data}")
        else:
            logger.error(f"Ошибка HTTP {response.status_code} при запросе к Telegram API: {response.text}")

        # Если произошла ошибка, возвращаем None
        logger.error(f"Не удалось получить ссылку для bot_file_id={self.bot_file.id}, file_id={self.bot_file.file_id}")
        return None
    
    @classmethod
    async def acreate_and_get_link(cls, bot_file, **kwargs):
        """Возвращает только ссылку"""
        from django.core.exceptions import ObjectDoesNotExist
        from django.db import IntegrityError
        
        try:
            cache = await cls.objects.aget(bot_file=bot_file)
            return await cache.aget_cache_link()
        except ObjectDoesNotExist:
            try:
                cache = cls(bot_file=bot_file, **kwargs)
                return await cache.aget_cache_link()
            except IntegrityError:
                try:
                    cache = await cls.objects.aget(bot_file=bot_file)
                    return await cache.aget_cache_link()
                except ObjectDoesNotExist:
                    return None

    async def aget_cache_link(self):
        """
        Асинхронно получает прямую ссылку на файл через Telegram Bot API.
        Сначала проверяет, не истекла ли текущая ссылка.
        Если истекла - обновляет кэш и возвращает новую ссылку.
        """
        import aiohttp

        bot_file_instance = await BotFile.objects.aget(id=self.bot_file_id)
        bot_instance = await Bot.objects.aget(id=bot_file_instance.bot_id)
        bot_token = bot_instance.token
        
        # Проверяем, не протухла ли текущая ссылка
        if not self.is_expired() and self.file_path:
            # Если не протухла - возвращаем существующую ссылку
            return f"https://api.telegram.org/file/bot{bot_token}/{self.file_path}"

        # Если протухла - получаем новую
        url = f"https://api.telegram.org/bot{bot_token}/getFile"

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, data={"file_id": bot_file_instance.file_id}
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("ok"):
                        # Обновляем кэш
                        self.file_path = data["result"]["file_path"]
                        # Обновляем время истечения (обычно через 1 час)
                        self.expires_at = timezone.now() + timezone.timedelta(hours=1)
                        await self.asave()  # Для Django 3.1+ с поддержкой асинхронного save

                        # Возвращаем новую ссылку
                        return f"https://api.telegram.org/file/bot{bot_token}/{self.file_path}"

        return None

    def __str__(self):
        return f"Cache for {self.file_path[:10]}"

    class Meta:
        verbose_name = f"{bot_prefix}: Кэш ссылок на файлы"
        verbose_name_plural = f"{bot_prefix}: Кэши ссылок на файлы"
