from django.contrib import admin
from tg_bot.admin import BotFileInline
from ..models.tarot import TarotCard, ExtendedMeaning, TarotMeaningCategory, TarotDeck, TarotCardItem


@admin.register(TarotCard)
class TarotCardAdmin(admin.ModelAdmin):
    list_display = ("card_id", "name", "is_major", "used_in_decks")
    list_display_links = ("name",)  # Кликабельно название, а не card_id
    list_editable = ("is_major",)
    list_filter = ("is_major",)
    search_fields = ("name", "card_id")

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('deck_cards__deck')

    @admin.display(description='Колоды')
    def used_in_decks(self, obj):
        decks = obj.deck_cards.select_related('deck')
        return len(decks)


class TarotCardItemInline(admin.TabularInline):
    model = TarotCardItem
    extra = 0
    show_change_link = True
    fields = ('tarot_card',)
    autocomplete_fields = ('tarot_card',)
    classes = ('collapse',)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('tarot_card')


@admin.register(TarotDeck)
class TarotDeckAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'is_active', 'cards_count', 'link')
    search_fields = ('name', 'slug')
    inlines = [TarotCardItemInline]

    def get_queryset(self, request):
        return self.model.all_decks.prefetch_related('cards')

    @admin.display(description='Карт в колоде')
    def cards_count(self, obj):
        return obj.cards.count()


@admin.register(TarotCardItem)
class TarotCardItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'deck', 'tarot_card', 'custom_name')
    list_filter = ('deck',)
    search_fields = ('deck__name', 'tarot_card__name', 'custom_name')
    autocomplete_fields = ('deck', 'tarot_card')
    inlines = [BotFileInline]
    fieldsets = (
        (None, {
            'fields': ('deck', 'tarot_card')
        }),
        ('Индивидуальные настройки', {
            'fields': ('custom_name', 'custom_description'),
            'classes': ('collapse',),
            'description': 'Необязательные поля для переопределения названия и описания карты в этой колоде'
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('deck', 'tarot_card')


@admin.register(ExtendedMeaning)
class ExtendedMeaningAdmin(admin.ModelAdmin):
    list_display = ("tarot_card", "category", "category_base", "text")
    search_fields = ("tarot_card__name", "text")
    list_filter = ("category", "category_base")
    autocomplete_fields = ("tarot_card",)


@admin.register(TarotMeaningCategory)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("__str__",)
    search_fields = ("name",)