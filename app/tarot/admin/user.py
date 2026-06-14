from django.contrib import admin
from ..models.user import UserReading


@admin.register(UserReading)
class UserReadingAdmin(admin.ModelAdmin):
    # Поля, которые будут отображаться в списке записей
    list_display = (
        "id",
        "user_link",
        "bot_link",           # Добавляем бота
        "category_display",   # Красивое отображение категории
        "card_count",         # Количество карт/рун в раскладе
        "created_at_short",   # Короткая дата
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