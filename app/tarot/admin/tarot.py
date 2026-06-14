from django.contrib import admin
from tg_bot.admin import BotFileInline
from ..models.tarot import TarotCard, ExtendedMeaning, TarotMeaningCategory, TarotDeck, TarotCardItem


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