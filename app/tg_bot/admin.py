from django.contrib import admin

from .models import Bot

@admin.register(Bot)
class BotAdmin(admin.ModelAdmin):
    list_display = ("name", "token", "chat_id", "bot_type", "created_at", "updated_at")
    search_fields = ("name", "token", "chat_id")
    list_filter = ("bot_type", "created_at")

