from django.contrib import admin
from django.utils.html import format_html
from django.utils.timezone import now
from ..models import AIApiKey


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
            "fields": ("bot", "title", "api_key")
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