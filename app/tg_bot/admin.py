from django.contrib import admin
from django.contrib.contenttypes.admin import GenericTabularInline
from django.utils.html import format_html

from .models import Bot, BotFile, BotFileCache, TgUser


@admin.register(Bot)
class BotAdmin(admin.ModelAdmin):
    list_display = ("name", "username_link", "token", "chat_id", "bot_type", "is_enabled", "docker_instance_name")
    search_fields = ("name", "username", "token", "chat_id")
    list_filter = ("bot_type", "is_enabled", "created_at")
    readonly_fields = ("username",)
    
    def username_link(self, obj):
        if obj.username:
            return format_html(
                '<a href="https://t.me/{}" target="_blank">@{}</a>',
                obj.username, obj.username
            )
        return "—"
    
    username_link.short_description = "Username"


class BotFileInline(GenericTabularInline):
    model = BotFile
    extra = 1  # Количество пустых полей для добавления
    autocomplete_fields = ("bot",)


class BotFileCacheInline(admin.TabularInline):
    """
    Инлайн для отображения кэша пути файла.
    """

    model = BotFileCache
    extra = 0  # Не показывать пустые формы для новых записей
    readonly_fields = (
        "file_path",
        "expires_at",
    )


@admin.register(BotFile)
class BotFileAdmin(admin.ModelAdmin):
    list_display = (
        "content_object",
        "bot",
        "file_id",
    )
    list_filter = ("bot", "content_type")
    search_fields = ("file_id",)
    autocomplete_fields = ("bot",)
    inlines = [
        BotFileCacheInline,
    ]


@admin.register(TgUser)
class TgUserAdmin(admin.ModelAdmin):
    # Отображение колонок в списке
    list_display = (
        "tg_id",
        "username",
        "first_name",
        "last_name",
        "language_code",
        "created_at",
    )

    # Поля, по которым работает поиск
    search_fields = ("tg_id", "username", "first_name", "last_name")

    # Фильтры в правой панели
    list_filter = ("is_bot", "language_code", "created_at")

    # Поля, доступные только для чтения (чтобы случайно не изменить системные данные)
    readonly_fields = ("created_at", "updated_at")

    # Опционально: группировка полей в форме редактирования
    fieldsets = (
        (
            "Основная информация",
            {"fields": ("tg_id", "username", "first_name", "last_name", "is_bot")},
        ),
        (
            "Системные данные",
            {
                "fields": ("language_code", "created_at", "updated_at"),
                "classes": ("collapse",),  # Свернет эту группу по умолчанию
            },
        ),
    )
