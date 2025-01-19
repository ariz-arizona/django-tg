from django.contrib import admin
from .models import Bot

@admin.register(Bot)
class BotAdmin(admin.ModelAdmin):
    list_display = ("name", "chat_id", "created_at", "updated_at")
    search_fields = ("name", "chat_id")
