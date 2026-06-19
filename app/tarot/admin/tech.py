from django.contrib import admin
from django.db.models import Count, Q
from django.utils.html import format_html
from django.utils.timezone import now
from ..models import AIApiKey, DeckSearch



@admin.register(AIApiKey)
class AIApiKeyAdmin(admin.ModelAdmin):
    # 1. Отображение списка (List View)
    list_display = (
        "title",
        "bot",
        "provider",
        "override_model_name",
        "status_badge",  # Красивый кастомный статус
        "is_active",
        "created_at",
    )
    
    list_filter = (
        "provider", 
        "is_active", 
        "is_exhausted", 
        "bot"
    )
    
    search_fields = (
        "title", 
        "api_key", 
        "override_model_name"
    )
    
    ordering = ("-created_at",)
    
    # Позволяет быстро включать/выключать ключи прямо из списка
    list_editable = ("is_active",)

    # 2. Форма редактирования (Form View) — группируем поля по смыслу
    fieldsets = (
        ("Основная информация", {
            "fields": ("bot", "title", "api_key", "system_prompt")
        }),
        ("Настройки провайдера и эндпоинта", {
            "fields": ("provider", "custom_base_url", "override_model_name", "project_identifier"),
            "description": "Выберите готового провайдера или укажите свой собственный URL."
        }),
        ("Статус и ограничения", {
            "fields": ("is_active", "is_exhausted", "exhausted_until"),
        }),
        ("Системные даты", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),  # Сворачиваемый блок
        }),
    )

    # Поля только для чтения
    readonly_fields = ("created_at", "updated_at")

    # 3. Кастомный метод для красивого вывода статуса (цветовые индикаторы)
    @admin.display(description="Текущий статус")
    def status_badge(self, obj):
        if not obj.is_active:
            return format_html('<span style="color: #ba2121; font-weight: bold;">❌ Отключен</span>')
        
        if obj.is_exhausted:
            if obj.exhausted_until and obj.exhausted_until > now():
                formatted_time = obj.exhausted_until.strftime("%d.%m %H:%M")
                return format_html(
                    '<span style="color: #ff9800; font-weight: bold;">⏳ Лимит до {}</span>', 
                    formatted_time
                )
            return format_html('<span style="color: #ba2121; font-weight: bold;">🚫 Лимит исчерпан</span>')
            
        return format_html('<span style="color: #26b99a; font-weight: bold;">🟢 Активен</span>')

    # 4. Массовые действия (Actions)
    actions = ["mark_as_active", "mark_as_inactive", "reset_exhausted"]

    @admin.action(description="Включить выбранные ключи")
    def mark_as_active(self, request, queryset):
        queryset.update(is_active=True)

    @admin.action(description="Выключить выбранные ключи")
    def mark_as_inactive(self, request, queryset):
        queryset.update(is_active=False)

    @admin.action(description="Сбросить статус исчерпания лимита")
    def reset_exhausted(self, request, queryset):
        queryset.update(is_exhausted=False, exhausted_until=None)
        

@admin.register(DeckSearch)
class DeckSearchAdmin(admin.ModelAdmin):
    list_display = [
        'deck_keyword', 
        'status', 
        'decks_found', 
        'created_at'
    ]
    list_filter = ['status', 'created_at']
    search_fields = ['deck_keyword']
    readonly_fields = ['deck_keyword', 'status', 'found_decks', 'created_at']
    date_hierarchy = 'created_at'
    
    # Агрегация внизу списка
    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context=extra_context)
        
        # Статистика
        total = DeckSearch.objects.count()
        success = DeckSearch.objects.filter(status='success').count()
        not_found = DeckSearch.objects.filter(status='not_found').count()
        multiple = DeckSearch.objects.filter(status='multiple_found').count()
        
        # Топ запросов
        top_keywords = DeckSearch.objects.values('deck_keyword')\
            .annotate(count=Count('id'))\
            .order_by('-count')[:10]
        
        # Топ "не найденных" запросов
        top_not_found = DeckSearch.objects.filter(status='not_found')\
            .values('deck_keyword')\
            .annotate(count=Count('id'))\
            .order_by('-count')[:10]
        
        # Процент успешных
        success_rate = round((success / total * 100), 1) if total > 0 else 0
        
        extra_context = extra_context or {}
        extra_context['stats'] = {
            'total': total,
            'success': success,
            'not_found': not_found,
            'multiple': multiple,
            'success_rate': success_rate,
            'top_keywords': top_keywords,
            'top_not_found': top_not_found,
        }
        
        response.context_data.update(extra_context)
        return response
    
    def decks_found(self, obj):
        if obj.found_decks:
            count = len(obj.found_decks)
            if count == 1:
                return obj.found_decks[0].get('name', '—')
            else:
                return f"{count} колод"
        return "—"
    decks_found.short_description = "Найдено"
    
    def has_add_permission(self, request):
        return False
    
    def has_change_permission(self, request, obj=None):
        return False
    
    def has_delete_permission(self, request, obj=None):
        return True  # Можно чистить логи