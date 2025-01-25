from django.contrib import admin
from .models import Bot, TgUser
from .models import ParseProduct, TgUserProduct
from .models import TarotCard, ExtendedMeaning, TarotDeck, TarotCardItem


@admin.register(Bot)
class BotAdmin(admin.ModelAdmin):
    list_display = ("name", "token", "chat_id", "created_at", "updated_at")
    search_fields = ("name", "token", "chat_id")
    list_filter = ("created_at", "updated_at")
    readonly_fields = ("created_at", "updated_at")


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
    list_display = ("name", "meaning")


@admin.register(ExtendedMeaning)
class ExtendedMeaningAdmin(admin.ModelAdmin):
    list_display = ("tarot_card", "category", "text")


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
    list_display = ("deck", "tarot_card",  "img_id")  # Поля, отображаемые в списке
    search_fields = ("deck__name", "tarot_card__name")  # Поля для поиска
    list_filter = ("deck", "tarot_card")  # Фильтры в правой панели