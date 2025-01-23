from django.contrib import admin
from .models import Bot, TgUser, ParseProduct, TgUserProduct

@admin.register(Bot)
class BotAdmin(admin.ModelAdmin):
    list_display = ('name', 'token', 'chat_id', 'created_at', 'updated_at')
    search_fields = ('name', 'token', 'chat_id')
    list_filter = ('created_at', 'updated_at')
    readonly_fields = ('created_at', 'updated_at')

@admin.register(TgUser)
class TgUserAdmin(admin.ModelAdmin):
    list_display = ('tg_id', 'username', 'first_name', 'last_name', 'language_code', 'is_bot', 'created_at', 'updated_at')
    search_fields = ('tg_id', 'username', 'first_name', 'last_name')
    list_filter = ('is_bot', 'created_at', 'updated_at')
    readonly_fields = ('created_at', 'updated_at')

@admin.register(ParseProduct)
class ParseProductAdmin(admin.ModelAdmin):
    list_display = ('product_id', 'photo_id', 'caption', 'product_type', 'created_at', 'updated_at')
    search_fields = ('product_id', 'caption')
    list_filter = ('product_type', 'created_at', 'updated_at')
    readonly_fields = ('created_at', 'updated_at')

@admin.register(TgUserProduct)
class TgUserProductAdmin(admin.ModelAdmin):
    list_display = ('tg_user', 'product', 'sent_at')
    search_fields = ('tg_user__username', 'product__caption')
    list_filter = ('sent_at',)
    readonly_fields = ('sent_at',)