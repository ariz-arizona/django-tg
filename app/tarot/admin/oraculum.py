from django.contrib import admin
from tg_bot.admin import BotFileInline
from ..models.oraculum import OraculumDeck, OraculumItem


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