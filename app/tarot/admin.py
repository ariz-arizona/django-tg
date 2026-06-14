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
    UserReading
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



@admin.register(UserReading)
class UserReadingAdmin(admin.ModelAdmin):
    # Поля, которые будут отображаться в списке записей
    list_display = (
        "id",
        "user_link",
        "bot_link",           
        "category_display",   
        "card_count",         
        "has_ai_interpretation",  # <-- НАША НОВАЯ ГАЛОЧКА ТУТ
        "created_at_short",   
    )
    
    # Боковая панель фильтрации
    list_filter = (
        "category",
        "bot",                # Фильтр по боту
        "is_flipped_allowed",
        "is_major_only",
        ("created_at", admin.DateFieldListFilter),  # Улучшенный фильтр по дате
    )

    # Поля поиска
    search_fields = (
        "user__username",
        "user__first_name", 
        "user__last_name",
        "user__tg_id",
        "bot__username",      # Поиск по юзернейму бота
        "text",
        "card_ids",           # Поиск по ID карт
    )

    # Только для чтения
    readonly_fields = ("created_at", "updated_at", "card_ids_preview")

    # Сортировка
    ordering = ("-created_at",)

    # Пагинация
    list_per_page = 50
    
    # Действия (можно добавить массовое удаление, экспорт)
    actions = ["export_selected"]

    # Поля для детального просмотра
    fieldsets = (
        ("Основная информация", {
            "fields": ("user", "bot", "category", "count")
        }),
        ("Настройки расклада", {
            "fields": ("is_flipped_allowed", "is_major_only", "deck_id"),
            "classes": ("collapse",)  # Сворачиваемый блок
        }),
        ("Результат", {
            "fields": ("text", "card_ids", "card_ids_preview", "message_id")
        }),
        ("Временные метки", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",)
        }),
    )
    
    
    # Добавляем кастомный метод отображения галочки
    @admin.display(
        boolean=True,                           # Превращает True/False в красивую иконку галочки/крестика
        ordering="ai_interpretations__id",       # Позволит сортировать список по наличию ИИ
        description="Есть ИИ толкование",        # Название колонки в админке
    )
    def has_ai_interpretation(self, obj):
        # Проверяем, есть ли хоть одна УСПЕШНАЯ генерация для этого расклада
        return obj.ai_interpretations.filter(status="success").exists()
        
        # Если нужно выводить галочку вообще при любой попытке (даже ошибочной), 
        # то замените строку выше на простую проверку:
        # return obj.ai_interpretations.exists()

    # Оптимизируем SQL-запросы, чтобы админка не легла от N+1 запросов
    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        # prefetch_related сделает один быстрый дополнительный запрос для пачки ИИ-связей
        return queryset.prefetch_related("ai_interpretations")

    def user_link(self, obj):
        if obj.user:
            # Ссылка на пользователя в админке
            from django.urls import reverse
            from django.utils.html import format_html
            
            url = reverse("admin:tg_bot_tguser_change", args=[obj.user.id])
            display_name = obj.user.username or obj.user.first_name or f"ID: {obj.user.tg_id}"
            return format_html('<a href="{}">{}</a>', url, display_name)
        return "—"
    
    user_link.short_description = "Пользователь"
    user_link.admin_order_field = "user__username"

    def bot_link(self, obj):
        if obj.bot:
            from django.urls import reverse
            from django.utils.html import format_html
            
            url = reverse("admin:tg_bot_bot_change", args=[obj.bot.id])
            display_name = obj.bot.username or f"Bot #{obj.bot.id}"
            return format_html('<a href="{}">{}</a>', url, display_name)
        return "—"
    
    bot_link.short_description = "Бот"
    bot_link.admin_order_field = "bot__username"

    def category_display(self, obj):
        """Цветное отображение категории"""
        from django.utils.html import format_html
        
        colors = {
            "one": "#4CAF50",      # Зеленый
            "tarot": "#9C27B0",    # Фиолетовый
            "oracle": "#2196F3",   # Синий
            "runes": "#FF9800",    # Оранжевый
            "canvas_spread": "#F44336",  # Красный
        }
        color = colors.get(obj.category, "#757575")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color, obj.get_category_display()
        )
    
    category_display.short_description = "Категория"
    category_display.admin_order_field = "category"

    def created_at_short(self, obj):
        """Короткий формат даты"""
        return obj.created_at.strftime("%d.%m.%Y %H:%M")
    
    created_at_short.short_description = "Дата"
    created_at_short.admin_order_field = "created_at"

    def card_count(self, obj):
        """Количество карт в раскладе"""
        count = len(obj.card_ids) if obj.card_ids else 0
        if count > 0:
            return f"🎴 {count}"
        return "—"
    
    card_count.short_description = "Карт"

    def card_ids_preview(self, obj):
        """Предпросмотр ID карт в детальном просмотре"""
        if obj.card_ids:
            return ", ".join(map(str, obj.card_ids[:10]))
        return "—"
    
    card_ids_preview.short_description = "Предпросмотр карт"

    def export_selected(self, request, queryset):
        """Экспорт выбранных записей в CSV"""
        import csv
        from django.http import HttpResponse
        
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="user_readings.csv"'
        
        writer = csv.writer(response)
        writer.writerow([
            "ID", "User ID", "Username", "Bot", "Category", 
            "Count", "Flipped", "Major Only", "Deck ID", 
            "Card IDs", "Created At", "Text Preview"
        ])
        
        for obj in queryset:
            writer.writerow([
                obj.id,
                obj.user.tg_id if obj.user else "",
                obj.user.username if obj.user else "",
                obj.bot.username if obj.bot else "",
                obj.get_category_display(),
                obj.count,
                obj.is_flipped_allowed,
                obj.is_major_only,
                obj.deck_id or "",
                ", ".join(map(str, obj.card_ids)) if obj.card_ids else "",
                obj.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                obj.text[:100] if obj.text else ""
            ])
        
        return response
    
    export_selected.short_description = "Экспортировать выбранные в CSV"