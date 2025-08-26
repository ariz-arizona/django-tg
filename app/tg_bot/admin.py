from django.contrib import admin
from django.utils.html import format_html

from .models import Bot, TgUser
from .models import ParseProduct, TgUserProduct, Brand, Category
from .models import (
    TarotCard,
    ExtendedMeaning,
    TarotDeck,
    TarotCardItem,
    TarotMeaningCategory,
    TarotUserReading,
    OraculumItem,
    OraculumDeck,
    Rune
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


@admin.register(OraculumItem)
class OraculumItemAdmin(admin.ModelAdmin):
    list_display = ("name", "deck", "description")
    search_fields = ("name", "description", "deck__name")
    list_filter = ("deck",)

@admin.register(Rune)
class RuneAdmin(admin.ModelAdmin):
    list_display = ("symbol", "type", "sticker")
    search_fields = ("type", "symbol")
    list_filter = ("type",)

    fieldsets = (
        ("Основная информация", {
            "fields": ("type", "symbol", "sticker"),
        }),
        ("Прямое значение", {
            "fields": (
                "straight_keys",
                "straight_meaning",
                "straight_pos_1",
                "straight_pos_2",
                "straight_pos_3",
            ),
        }),
        ("Перевернутое значение", {
            "fields": (
                "inverted_keys",
                "inverted_meaning",
                "inverted_pos_1",
                "inverted_pos_2",
                "inverted_pos_3",
            ),
        }),
    )
    

# === Админка: Brand ===
@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "brand_id",
        "product_type",
        "products_count",
        "created_at",
    )
    list_filter = ("product_type", "created_at")
    search_fields = ("name", "brand_id")
    readonly_fields = ("created_at",)
    fieldsets = (
        (
            "Основная информация",
            {
                "fields": ("name", "brand_id", "product_type"),
            },
        ),
        (
            "Аудит",
            {
                "fields": ("created_at",),
            },
        ),
    )
    ordering = ("name",)

    def products_count(self, obj):
        """Отображает количество товаров этого бренда"""
        count = obj.parseproduct_set.count()
        url = (
            f"/admin/parser/parseproduct/?brand__id={obj.id}"
            if hasattr(obj, "parseproduct_set")
            else "#"
        )
        return format_html(
            '<a href="{}" style="text-decoration: none;">🧩 <strong>{}</strong></a>',
            url,
            count,
        )

    products_count.short_description = "Товары"
    products_count.allow_tags = True


# === Админка: Category ===
@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "subject_id",
        "product_type",
        "products_count",
        "created_at",
    )
    list_filter = ("product_type", "created_at")
    search_fields = ("name", "subject_id")
    readonly_fields = ("created_at",)
    fieldsets = (
        (
            "Категория",
            {
                "fields": ("name", "subject_id", "product_type"),
            },
        ),
        (
            "Аудит",
            {
                "fields": ("created_at",),
            },
        ),
    )
    ordering = ("name",)

    def products_count(self, obj):
        """Количество товаров в категории"""
        count = obj.parseproduct_set.count()
        url = f"/admin/parser/parseproduct/?category__id={obj.id}"
        return format_html(
            '<a href="{}" style="text-decoration: none;">📦 <strong>{}</strong></a>',
            url,
            count,
        )

    products_count.short_description = "Товары"
    products_count.allow_tags = True