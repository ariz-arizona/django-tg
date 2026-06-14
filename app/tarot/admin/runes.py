from django.contrib import admin
from ..models.runes import Rune


@admin.register(Rune)
class RuneAdmin(admin.ModelAdmin):
    list_display = ("symbol", "type", "sticker")
    search_fields = ("type", "symbol")
    list_filter = ("type",)

    fieldsets = (
        (
            "Основная информация",
            {
                "fields": ("type", "symbol", "sticker"),
            },
        ),
        (
            "Прямое значение",
            {
                "fields": (
                    "straight_keys",
                    "straight_meaning",
                    "straight_pos_1",
                    "straight_pos_2",
                    "straight_pos_3",
                ),
            },
        ),
        (
            "Перевернутое значение",
            {
                "fields": (
                    "inverted_keys",
                    "inverted_meaning",
                    "inverted_pos_1",
                    "inverted_pos_2",
                    "inverted_pos_3",
                ),
            },
        ),
    )