import csv
from django.contrib import admin
from django.http import HttpResponse
from django.urls import reverse
from django.utils.html import format_html

from ..models.user import UserReading, AIReadingInterpretation, AIReadingPage
from ..models.tech import AIApiKey


class AIReadingInterpretationInline(admin.TabularInline):
    """Инлайн для отображения ИИ-генераций прямо внутри расклада карт"""
    model = AIReadingInterpretation
    extra = 0
    # Делаем поля только для чтения, чтобы случайно не сломать логи токенов вручную
    readonly_fields = (
        "status_display", 
        "model_used", 
        "tokens_summary", 
        "response_preview", 
        "created_at"
    )
    # Поля, которые будут видны в строке таблицы инлайна
    fields = ("status_display", "model_used", "tokens_summary", "response_preview", "created_at")
    can_delete = False
    
    def has_add_permission(self, request, obj=None):
        return False  # ИИ-запросы создаются только кодом бота

    def status_display(self, obj):
        colors = {
            "pending": "#FF9800",  # Оранжевый
            "success": "#4CAF50",  # Зеленый
            "failed": "#F44336",   # Красный
        }
        color = colors.get(obj.status, "#757575")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color, obj.get_status_display()
        )
    status_display.short_description = "Статус"

    def tokens_summary(self, obj):
        if obj.status == "success":
            return format_html(
                "📥 {} / 📤 {} <br><small style='color:#757575'>Всего: {}</small>",
                obj.prompt_tokens, obj.completion_tokens, obj.total_tokens
            )
        return "—"
    tokens_summary.short_description = "Токены (In/Out)"

    def response_preview(self, obj):
        # Получаем склеенный текст из связанных чанков
        # Используем values_list flat=True для эффективности
        chunks = obj.pages.values_list('content', flat=True)
        full_text = "".join(chunks)
        
        if obj.status == "success" and full_text:
            return full_text[:60] + ("..." if len(full_text) > 60 else "")
        
        if obj.status == "failed" and obj.error_message:
            return format_html('<span style="color: red;">⚠ {}</span>', obj.error_message[:60])
        
        return "—"
    
    response_preview.short_description = "Превью ответа"


@admin.register(UserReading)
class UserReadingAdmin(admin.ModelAdmin):
    # Добавляем в инлайны нашу ИИ-модель
    inlines = [AIReadingInterpretationInline]

    list_display = (
        "id",
        "user_link",
        "bot_link",           
        "category_display",   
        "card_count",         
        "ai_status_summary",  # Добавили агрегированный статус ИИ в общий список
        "created_at_short",   
    )

    list_filter = (
        "category",
        "bot",                
        "is_flipped_allowed",
        "is_major_only",
        "ai_interpretations__status",  # Позволяет фильтровать расклады по статусу ИИ-запросов
        ("created_at", admin.DateFieldListFilter),  
    )

    search_fields = (
        "user__username",
        "user__first_name", 
        "user__last_name",
        "user__tg_id",
        "bot__username",      
        "text",
        "card_ids",  
    )

    readonly_fields = ("created_at", "updated_at", "card_ids_preview")
    ordering = ("-created_at",)
    list_per_page = 50
    actions = ["export_selected"]

    fieldsets = (
        ("Основная информация", {
            "fields": ("user", "bot", "category", "count", "original_query")
        }),
        ("Настройки расклада", {
            "fields": ("is_flipped_allowed", "is_major_only", "deck_id"),
            "classes": ("collapse",)  
        }),
        ("Результат карт", {
            "fields": ("text", "card_ids", "card_ids_preview", "message_id")
        }),
        ("Временные метки", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",)
        }),
    )

    def user_link(self, obj):
        if obj.user:
            url = reverse("admin:tg_bot_tguser_change", args=[obj.user.id])
            display_name = obj.user.username or obj.user.first_name or f"ID: {obj.user.tg_id}"
            return format_html('<a href="{}">{}</a>', url, display_name)
        return "—"
    user_link.short_description = "Пользователь"
    user_link.admin_order_field = "user__username"

    def bot_link(self, obj):
        if obj.bot:
            url = reverse("admin:tg_bot_bot_change", args=[obj.bot.id])
            display_name = obj.bot.username or f"Bot #{obj.bot.id}"
            return format_html('<a href="{}">{}</a>', url, display_name)
        return "—"
    bot_link.short_description = "Бот"
    bot_link.admin_order_field = "bot__username"

    def category_display(self, obj):
        colors = {
            "one": "#4CAF50",      
            "tarot": "#9C27B0",    
            "oracle": "#2196F3",   
            "runes": "#FF9800",    
            "canvas_spread": "#F44336",  
        }
        color = colors.get(obj.category, "#757575")
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color, obj.get_category_display()
        )
    category_display.short_description = "Категория"
    category_display.admin_order_field = "category"

    def ai_status_summary(self, obj):
        """Выводит в общий список статус последней интерпретации ИИ и тотал токенов"""
        latest_ai = obj.ai_interpretations.order_by("-created_at").first()
        if not latest_ai:
            return format_html('<span style="color: #757575;">Без ИИ</span>')
        
        status_colors = {"pending": "#FF9800", "success": "#4CAF50", "failed": "#F44336"}
        color = status_colors.get(latest_ai.status, "#757575")
        
        if latest_ai.status == "success":
            return format_html(
                '<span style="color: {}; font-weight: bold;">🤖 OK</span> <small style="color: #757575">({} tkn)</small>',
                color, latest_ai.total_tokens
            )
        return format_html('<span style="color: {}; font-weight: bold;">🤖 {}</span>', color, latest_ai.get_status_display())
    ai_status_summary.short_description = "ИИ Ответ"

    def created_at_short(self, obj):
        return obj.created_at.strftime("%d.%m.%Y %H:%M")
    created_at_short.short_description = "Дата"
    created_at_short.admin_order_field = "created_at"

    def card_count(self, obj):
        count = len(obj.card_ids) if obj.card_ids else 0
        return f"🎴 {count}" if count > 0 else "—"
    card_count.short_description = "Карт"

    def card_ids_preview(self, obj):
        if obj.card_ids:
            return ", ".join(map(str, obj.card_ids[:10]))
        return "—"
    card_ids_preview.short_description = "Предпросмотр карт"

    def export_selected(self, request, queryset):
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


class AIReadingPageInline(admin.TabularInline):
    model = AIReadingPage
    extra = 0  # Не показывать пустые поля для новых записей по умолчанию
    readonly_fields = ("page_number", "content", "created_at")
    can_delete = False  # Обычно чанки не стоит удалять отдельно от интерпретации
    show_change_link = False
    ordering = ("page_number",)
    
@admin.register(AIReadingInterpretation)
class AIReadingInterpretationAdmin(admin.ModelAdmin):
    """Отдельная админка для глубокого анализа ИИ запросов и разбора ошибок"""
    list_display = ("id", "status", "model_used", "prompt_tokens", "completion_tokens", "total_tokens", "created_at")
    list_filter = ("status", "model_used", "created_at")
    search_fields = ("reading__id", "prompt_user", "error_message")
    ordering = ("-created_at",)
    
    inlines = [AIReadingPageInline]
    
    readonly_fields = ("created_at", "updated_at")
    
    fieldsets = (
        ("Связи и Метаданные", {
            "fields": ("reading", "ai_key", "model_used", "status")
        }),
        ("Слои Промптов", {
            "fields": ("prompt_system", "prompt_user"),
        }),
        ("Результат выполнения", {
            "fields": ("error_message",),
        }),
        ("Статистика токенов", {
            "fields": ("prompt_tokens", "completion_tokens", "total_tokens"),
        }),
        ("Логи времени", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",)
        }),
    )