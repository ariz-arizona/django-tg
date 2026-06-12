from django.contrib import admin

from .models import (
    TarotCard,
    ExtendedMeaning,
    TarotDeck,
    TarotCardItem,
    TarotMeaningCategory,
    OraculumItem,
    OraculumDeck,
    Rune,
)
from tg_bot.admin import BotFileInline

@admin.register(TarotCard)
class TarotCardAdmin(admin.ModelAdmin):
    list_display = ("card_id", "name", "is_major")  # Добавлено card_id для наглядности
    list_editable = ("is_major",)  # Разрешаем редактирование прямо в списке
    list_filter = ("is_major",)  # Добавляем фильтр по этому полю
    search_fields = ("name", "card_id")  # Поиск по названию и ID карты


@admin.register(ExtendedMeaning)
class ExtendedMeaningAdmin(admin.ModelAdmin):
    list_display = ("tarot_card", "category", "category_base", "text")


@admin.register(TarotMeaningCategory)
class CategoryAdmin(admin.ModelAdmin):
    pass


@admin.register(TarotDeck)
class TarotDeckAdmin(admin.ModelAdmin):
    """
    Админка для модели TarotDeck.
    """

    list_display = ("name", "link")  # Поля, отображаемые в списке
    search_fields = ("name",)  # Поля для поиска
    list_filter = ("name",)  # Фильтры в правой панели


@admin.register(TarotCardItem)
class CardAdmin(admin.ModelAdmin):
    """
    Админка для модели Card.
    """

    list_display = (
        "tarot_card",
        "deck",
    )  # Поля, отображаемые в списке
    search_fields = ("deck__name", "tarot_card__name")  # Поля для поиска
    list_filter = ("deck", "tarot_card")  # Фильтры в правой панели
    autocomplete_fields = (
        "deck",
        "tarot_card",
    )
    inlines = [
        BotFileInline,
    ]


@admin.register(OraculumDeck)
class OraculumDeckAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "created_at")
    search_fields = ("name", "description")
    list_filter = ("created_at",)


@admin.register(OraculumItem)
class OraculumItemAdmin(admin.ModelAdmin):
    list_display = ("name", "deck", "description")
    search_fields = ("name", "description", "deck__name")
    list_filter = ("deck",)
    inlines = [
        BotFileInline,
    ]


@admin.register(Rune)
class RuneAdmin(admin.ModelAdmin):
    list_display = ("symbol", "type", "sticker")
    search_fields = ("type", "symbol")
    list_filter = ("type",)

    fieldsets = (
        (
            "Основная информация",
            {
                "fields": ("type", "symbol", "sticker"),
            },
        ),
        (
            "Прямое значение",
            {
                "fields": (
                    "straight_keys",
                    "straight_meaning",
                    "straight_pos_1",
                    "straight_pos_2",
                    "straight_pos_3",
                ),
            },
        ),
        (
            "Перевернутое значение",
            {
                "fields": (
                    "inverted_keys",
                    "inverted_meaning",
                    "inverted_pos_1",
                    "inverted_pos_2",
                    "inverted_pos_3",
                ),
            },
        ),
    )

from django.contrib import admin
from .models import UserReading


@admin.register(UserReading)
class UserReadingAdmin(admin.ModelAdmin):
    # Поля, которые будут отображаться в списке записей
    list_display = (
        "id",
        "user_link",       # Красивая ссылка на пользователя (если настроена) или просто юзер
        "category",
        "deck_id",
        "is_flipped_allowed",
        "is_major_only",
        "created_at",
    )

    # Боковая панель фильтрации (очень удобно для аналитики)
    list_filter = (
        "category",
        "is_flipped_allowed",
        "is_major_only",
        "created_at",
    )

    # Поля, по которым будет работать поиск вверху страницы
    # (ищет по ID телеграм-юзера, юзернейму или тексту самого расклада)
    search_fields = (
        "user__username", 
        "user__tg_id", 
        "text"
    )

    # Поля, доступные только для чтения (логи активности обычно не редактируют вручную)
    readonly_fields = ("created_at", "updated_at")

    # Сортировка по умолчанию: сначала самые новые расклады
    ordering = ("-created_at",)

    # Ограничение на количество записей на одной странице
    list_per_page = 50

    # Метод для отображения пользователя (если у модели TgUser есть метод get_absolute_url)
    def user_link(self, obj):
        if obj.user:
            return obj.user.username or f"ID: {obj.user.pk}"
        return "Unknown User"
    
    user_link.short_description = "Пользователь"