from django.contrib import admin
from django.utils.html import format_html
from tg_bot.admin import BotFileInline
from ..models.oraculum import OraculumDeck, OraculumItem


class OraculumItemInline(admin.TabularInline):
    model = OraculumItem
    extra = 0
    show_change_link = True
    fields = ('name',)
    classes = ('collapse',)


@admin.register(OraculumDeck)
class OraculumDeckAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'cards_count', 'description_preview', 'created_at')
    search_fields = ('name', 'slug', 'description')
    list_filter = ('created_at',)
    inlines = [OraculumItemInline]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('cards')

    @admin.display(description='Карт в колоде')
    def cards_count(self, obj):
        return obj.cards.count()

    @admin.display(description='Описание')
    def description_preview(self, obj):
        if obj.description:
            return obj.description[:80] + '…' if len(obj.description) > 80 else obj.description
        return '—'


@admin.register(OraculumItem)
class OraculumItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'deck', 'description_preview', 'has_values')
    search_fields = ('name', 'description', 'deck__name')
    list_filter = ('deck',)
    autocomplete_fields = ('deck',)
    inlines = [BotFileInline]
    fieldsets = (
        ('Основное', {
            'fields': ('deck', 'name', 'description')
        }),
        ('Значения', {
            'fields': ('direct', 'inverted'),
            'classes': ('collapse',),
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('deck')

    @admin.display(description='Описание')
    def description_preview(self, obj):
        if obj.description:
            return obj.description[:80] + '…' if len(obj.description) > 80 else obj.description
        return '—'

    @admin.display(description='Значения', boolean=True)
    def has_values(self, obj):
        return bool(obj.direct or obj.inverted)