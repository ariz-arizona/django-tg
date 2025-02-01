from django.contrib import admin
from .models import Bot, TgUser
from .models import ParseProduct, TgUserProduct
from .models import (
    TarotCard,
    ExtendedMeaning,
    TarotDeck,
    TarotCardItem,
    TarotMeaningCategory,
    TarotUserReading,
    Oraculum,
    OraculumDeck,
)


@admin.register(Bot)
class BotAdmin(admin.ModelAdmin):
    list_display = ("name", "token", "chat_id", "bot_type", "created_at", "updated_at")
    search_fields = ("name", "token", "chat_id")
    list_filter = ("bot_type", "created_at")


@admin.register(ParseProduct)
class ParseProductAdmin(admin.ModelAdmin):
    list_display = ("product_id", "caption", "product_type", "created_at")
    list_filter = ("product_type",)
    search_fields = ("caption", "product_id")


@admin.register(TgUserProduct)
class TgUserProductAdmin(admin.ModelAdmin):
    list_display = ("tg_user", "product", "sent_at")
    list_filter = ("sent_at",)
    search_fields = ("tg_user__username", "product__caption")


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

    list_display = ("deck", "tarot_card", "img_id")  # Поля, отображаемые в списке
    search_fields = ("deck__name", "tarot_card__name")  # Поля для поиска
    list_filter = ("deck", "tarot_card")  # Фильтры в правой панели


@admin.register(TarotUserReading)
class TarotUserReadingAdmin(admin.ModelAdmin):
    # Поля, которые будут отображаться в списке записей
    list_display = ("user", "date", "text", "message_id")

    # Поля, по которым можно фильтровать записи
    list_filter = ("user", "date")

    # Поля, по которым можно искать записи
    search_fields = ("text", "user__username", "message_id")

    # Поля, которые будут использоваться для детального просмотра записи
    fieldsets = (
        (
            None,
            {
                "fields": ("user", "date", "text", "message_id"),
            },
        ),
    )

    # Автоматическое заполнение поля даты при создании записи
    def get_readonly_fields(self, request, obj=None):
        if obj:  # Если объект уже существует, запрещаем редактирование даты
            return self.readonly_fields + ("date",)
        return self.readonly_fields


@admin.register(OraculumDeck)
class OraculumDeckAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "created_at")
    search_fields = ("name", "description")
    list_filter = ("created_at",)


@admin.register(Oraculum)
class OraculumAdmin(admin.ModelAdmin):
    list_display = ("name", "deck", "description")
    search_fields = ("name", "description", "deck__name")
    list_filter = ("deck",)
