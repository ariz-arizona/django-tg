# roster/admin.py
from django.contrib import admin
from django.utils.html import format_html

from tg_bot.admin import BotFileInline
from .models.team import Season, Team, Card
from .models.roll import UserRoll, RosterUser
from .models.tech import RollLimit, RarityWeight


class TeamInline(admin.TabularInline):
    model = Team
    extra = 0
    show_change_link = True


class CardInline(admin.TabularInline):
    model = Card
    extra = 0
    show_change_link = True


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ["name", "start_date", "end_date", "is_active", "team_count"]
    list_filter = ["is_active"]
    search_fields = ["name"]
    inlines = [TeamInline]

    def team_count(self, obj):
        return obj.teams.count()

    team_count.short_description = "Команд"


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ["name", "stars_display", "season", "card_count"]
    list_filter = ["season", "stars"]
    search_fields = ["name"]
    inlines = [CardInline, BotFileInline,]

    def stars_display(self, obj):
        return "⭐" * obj.stars

    stars_display.short_description = "Звёздность"

    def card_count(self, obj):
        return obj.cards.count()

    card_count.short_description = "Карт"
    


# roster/admin.py

from django.contrib.contenttypes.admin import GenericStackedInline
from tg_bot.models import BotFile


class CardImageInline(GenericStackedInline):
    """Инлайн для открытой картинки (image)"""

    model = BotFile
    extra = 0
    ct_field = "content_type"
    ct_fk_field = "object_id"
    verbose_name = "Открытая картинка"
    verbose_name_plural = "Открытые картинки"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # Фильтруем только те, что привязаны через image
        return qs.filter(content_type__model="card")


class CardImageHiddenInline(GenericStackedInline):
    """Инлайн для скрытой картинки (image_hidden)"""

    model = BotFile
    extra = 0
    ct_field = "content_type"
    ct_fk_field = "object_id"
    verbose_name = "Скрытая картинка"
    verbose_name_plural = "Скрытые картинки"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(content_type__model="card")


@admin.register(Card)
class CardAdmin(admin.ModelAdmin):
    list_display = ["name", "stars_display", "team", "team_season"]
    list_filter = ["team__season", "team", "stars"]
    search_fields = ["name", "description"]
    inlines = [CardImageInline, CardImageHiddenInline]

    def stars_display(self, obj):
        return "⭐" * obj.stars

    stars_display.short_description = "Звёздность"

    def team_season(self, obj):
        return obj.team.season.name

    team_season.short_description = "Сезон"


@admin.register(UserRoll)
class UserRollAdmin(admin.ModelAdmin):
    list_display = ["user", "card", "get_team", "get_season", "bot", "rolled_at"]
    list_filter = ["bot", "season", "rolled_at"]
    search_fields = ["user__username", "user__first_name", "user__tg_id", "card__name"]
    readonly_fields = ["user", "bot", "card", "season", "get_team", "rolled_at"]
    date_hierarchy = "rolled_at"

    @admin.display(description="Команда", ordering="card__team")
    def get_team(self, obj):
        return obj.card.team.name

    @admin.display(description="Сезон", ordering="season")
    def get_season(self, obj):
        return obj.season.name

    def has_add_permission(self, request):
        return False

@admin.register(RosterUser)
class RosterUserAdmin(admin.ModelAdmin):
    # Поля, которые отображаются в списке пользователей гачи
    list_display = (
        'get_tg_id', 
        'get_username', 
        'get_full_name', 
        'is_premium'
    )
    
    # Быстрые фильтры в правой панели
    list_filter = ('is_premium',)
    
    # Поиск. Так как связь OneToOne, ищем по полям связанной модели TgUser
    search_fields = (
        'user__tg_id', 
        'user__username', 
        'user__first_name', 
        'user__last_name',
        'description'
    )
    
    # Чтобы случайно не повесить базу при дропдауне, если пользователей много
    raw_id_fields = ('user',)

    # Вычисляемые поля для красивого отображения данных из базового TgUser в списке
    @admin.display(ordering='user__tg_id', description='Telegram ID')
    def get_tg_id(self, obj):
        return obj.user.tg_id

    @admin.display(ordering='user__username', description='Username')
    def get_username(self, obj):
        return f"@{obj.user.username}" if obj.user.username else "—"

    @admin.display(description='Имя Фамилия')
    def get_full_name(self, obj):
        parts = [obj.user.first_name, obj.user.last_name]
        return " ".join([p for p in parts if p]) or "—"
    
    
@admin.register(RollLimit)
class RollLimitAdmin(admin.ModelAdmin):
    list_display = ["limit_type", "is_premium", "bot", "value"]
    list_filter = ["bot", "limit_type"]
    search_fields = ["bot__name", "bot__username", "limit_type"]
    list_editable = ["value"]

@admin.register(RarityWeight)
class RarityWeightAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'bot',
        'enabled_status',
        'coefficient',
        'formula_preview',
        'weights_summary',
        'created_at'
    ]
    
    list_filter = [
        'bot',
        'enabled',
    ]
    
    search_fields = [
        'bot__name',
        'formula',
    ]
    
    readonly_fields = [
        'created_at',
        'updated_at',
        'weights_preview',
        'probabilities_preview',
    ]
    
    fieldsets = (
        ('Основные настройки', {
            'fields': (
                'bot',
                'enabled',
                'formula',
                'coefficient',
            )
        }),
        ('Предпросмотр весов', {
            'fields': (
                'weights_preview',
                'probabilities_preview',
            )
        }),
        ('Системная информация', {
            'fields': (
                'created_at',
                'updated_at',
            ),
            'classes': ('collapse',)
        }),
    )
    
    list_per_page = 20
    actions = ['enable_selected', 'disable_selected', 'reset_to_default_formula']
    
    def enabled_status(self, obj):
        """Статус активности с иконкой"""
        if obj.enabled:
            return format_html(
                '<span style="color: green; font-weight: bold;">✓ Активен</span>'
            )
        return format_html(
            '<span style="color: red;">✗ Неактивен</span>'
        )
    enabled_status.short_description = 'Статус'
    enabled_status.admin_order_field = 'enabled'
    
    def formula_preview(self, obj):
        """Краткое отображение формулы"""
        if len(obj.formula) > 50:
            return f"{obj.formula[:50]}..."
        return obj.formula
    formula_preview.short_description = 'Формула'
    
    def weights_summary(self, obj):
        """Краткая сводка весов"""
        try:
            weights = obj.get_all_weights()
            parts = []
            for star in range(1, 6):
                parts.append(f"★{star}: {weights[star]:.4f}")
            return " | ".join(parts)
        except Exception as e:
            return format_html(
                '<span style="color: red;">Ошибка</span>'
            )
    weights_summary.short_description = 'Веса (★1-★5)'
    
    def weights_preview(self, obj):
        """Предпросмотр весов в виде таблицы"""
        if not obj.pk:
            return "Сохраните объект для просмотра"
        
        try:
            weights = obj.get_all_weights()
            probabilities = obj.get_probabilities()
        except:
            return format_html('<span style="color: red;">Ошибка вычисления</span>')
        
        html = '''
        <table style="border-collapse: collapse; width: 100%;">
            <tr style="background-color: #f0f0f0;">
                <th style="padding: 8px; border: 1px solid #ddd;">Редкость</th>
                <th style="padding: 8px; border: 1px solid #ddd;">Вес</th>
                <th style="padding: 8px; border: 1px solid #ddd;">Вероятность</th>
            </tr>
        '''
        
        colors = {
            1: '#808080',  # серый
            2: '#00ff00',  # зеленый
            3: '#0080ff',  # синий
            4: '#a020f0',  # фиолетовый
            5: '#ffd700',  # золотой
        }
        
        for star in range(1, 6):
            color = colors[star]
            html += '<tr>'
            html += f'<td style="padding: 8px; border: 1px solid #ddd; color: {color}; font-size: 16px;">{"★" * star}</td>'
            html += f'<td style="padding: 8px; border: 1px solid #ddd;">{weights[star]:.10f}</td>'
            html += f'<td style="padding: 8px; border: 1px solid #ddd;"><strong>{probabilities[star]:.2f}%</strong></td>'
            html += '</tr>'
        
        html += f'''
            <tr style="background-color: #f9f9f9;">
                <td style="padding: 8px; border: 1px solid #ddd;"><strong>Итого</strong></td>
                <td style="padding: 8px; border: 1px solid #ddd;"><strong>{sum(weights.values()):.4f}</strong></td>
                <td style="padding: 8px; border: 1px solid #ddd;"><strong>100%</strong></td>
            </tr>
        '''
        
        html += '</table>'
        return format_html(html)
    weights_preview.short_description = 'Веса'
    
    def probabilities_preview(self, obj):
        """Визуализация вероятностей"""
        if not obj.pk:
            return ""
        
        try:
            probs = obj.get_probabilities()
        except:
            return ""
        
        colors = {
            1: '#808080',
            2: '#00ff00',
            3: '#0080ff',
            4: '#a020f0',
            5: '#ffd700',
        }
        
        html = '<div style="margin-top: 10px;">'
        for star in range(1, 6):
            prob = probs[star]
            color = colors[star]
            html += f'''
            <div style="margin-bottom: 8px;">
                <div style="display: flex; align-items: center; margin-bottom: 2px;">
                    <span style="color: {color}; width: 50px;">{"★" * star}</span>
                    <span style="width: 60px;">{prob:.2f}%</span>
                </div>
                <div style="background-color: #e0e0e0; height: 20px; border-radius: 3px; width: 100%;">
                    <div style="background-color: {color}; height: 100%; width: {prob}%; border-radius: 3px;
                         min-width: 2px; transition: width 0.3s;"></div>
                </div>
            </div>
            '''
        html += '</div>'
        return format_html(html)
    probabilities_preview.short_description = 'Распределение вероятностей'
    
    def enable_selected(self, request, queryset):
        """Активировать выбранную запись, деактивировав остальные для бота"""
        if queryset.count() > 1:
            self.message_user(
                request,
                "Можно активировать только одну запись за раз.",
                level='ERROR'
            )
            return
        
        obj = queryset.first()
        RarityWeight.objects.filter(bot=obj.bot, enabled=True).update(enabled=False)
        queryset.update(enabled=True)
        self.message_user(request, f"Запись активирована для бота {obj.bot}.")
    enable_selected.short_description = "✓ Активировать выбранные"
    
    def disable_selected(self, request, queryset):
        """Деактивировать выбранные записи"""
        count = queryset.update(enabled=False)
        self.message_user(request, f"Деактивировано записей: {count}")
    disable_selected.short_description = "✗ Деактивировать выбранные"
    
    def reset_to_default_formula(self, request, queryset):
        """Сбросить формулу и коэффициент на значения по умолчанию"""
        updated = queryset.update(
            formula="1 / (math.factorial({star}) * ({star} + 1))",
            coefficient=1.0
        )
        self.message_user(request, f"Сброшено записей: {updated}")
    reset_to_default_formula.short_description = "↺ Сбросить на стандартные"
    
    def save_model(self, request, obj, form, change):
        """
        При сохранении: если запись активна, деактивируем другие для этого бота
        """
        if obj.enabled:
            RarityWeight.objects.filter(
                bot=obj.bot,
                enabled=True
            ).exclude(
                pk=obj.pk
            ).update(enabled=False)
        
        super().save_model(request, obj, form, change)
    
    def get_queryset(self, request):
        return super().get_queryset(request).select_related('bot')