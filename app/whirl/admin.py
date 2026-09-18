import json

from django.contrib import admin
from django.utils.html import format_html

from .models import SirenRecord, SirenRecordImage, SirenRecordSound, WhirlUser, SirenAttempt


# --- Inline-модели для отображения ассетов прямо в карточке записи сирены ---

class SirenRecordImageInline(admin.StackedInline):
    model = SirenRecordImage
    extra = 0
    max_num = 1
    can_delete = True
    verbose_name = "Изображение паттерна"
    verbose_name_plural = "Изображение паттерна"


class SirenRecordSoundInline(admin.StackedInline):
    model = SirenRecordSound
    extra = 0
    max_num = 1
    can_delete = True
    verbose_name = "Аудиозапись паттерна"
    verbose_name_plural = "Аудиозапись паттерна"


# --- Основные классы админки ---

@admin.register(WhirlUser)
class WhirlUserAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "get_telegram_info",
        "is_admin",
        "siren_records_count",
        "created_at",
    )
    list_display_links = ("id", "get_telegram_info")
    list_filter = ("is_admin", "created_at")
    search_fields = (
        "user__username",
        "user__first_name",
        "user__last_name",
        "user__telegram_id",
    )
    raw_id_fields = ("user",)
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Telegram Пользователь")
    def get_telegram_info(self, obj):
        user = getattr(obj, "user", None)
        if not user:
            return "—"
        username = getattr(user, "username", None)
        tg_id = getattr(user, "telegram_id", getattr(user, "tg_id", user.pk))
        return f"@{username} ({tg_id})" if username else f"ID: {tg_id}"

    @admin.display(description="Записей сирен")
    def siren_records_count(self, obj):
        return obj.siren_records.count()


@admin.register(SirenRecord)
class SirenRecordAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "slug",
        "created_by",
        "is_active",
        "has_image",
        "has_sound",
        "created_at",
    )
    list_display_links = ("title", "slug")
    list_filter = ("is_active", "created_at")
    search_fields = ("title", "slug")
    prepopulated_fields = {"slug": ("title",)}
    raw_id_fields = ("created_by",)
    readonly_fields = (
        "created_at",
        "updated_at",
        "pretty_generated_sequence",
        "pretty_normalized_curve",
    )
    inlines = [SirenRecordImageInline, SirenRecordSoundInline]

    fieldsets = (
        (
            "Основная информация",
            {
                "fields": (
                    "title",
                    "slug",
                    "created_by",
                    "is_active",
                )
            },
        ),
        (
            "Данные паттернов и кривых",
            {
                "classes": ("collapse",),
                "fields": (
                    "pretty_generated_sequence",
                    "pretty_normalized_curve",
                    "generated_sequence",
                    "normalized_curve",
                ),
            },
        ),
        (
            "Служебная информация",
            {
                "classes": ("collapse",),
                "fields": ("created_at", "updated_at"),
            },
        ),
    )

    @admin.display(description="Картинка", boolean=True)
    def has_image(self, obj):
        # Проверяем наличие связанного объекта SirenRecordImage
        return hasattr(obj, "image_asset") and obj.image_asset is not None

    @admin.display(description="Аудио", boolean=True)
    def has_sound(self, obj):
        # Проверяем наличие связанного объекта SirenRecordSound
        return hasattr(obj, "sound_asset") and obj.sound_asset is not None

    @admin.display(description="Сгенерированная последовательность (pretty JSON)")
    def pretty_generated_sequence(self, obj):
        return self._format_json(obj.generated_sequence)

    @admin.display(description="Нормализованная кривая (pretty JSON)")
    def pretty_normalized_curve(self, obj):
        return self._format_json(obj.normalized_curve)

    def _format_json(self, data):
        if not data:
            return "—"
        pretty_data = json.dumps(data, ensure_ascii=False, indent=2)
        return format_html(
            "<pre style='max-height: 300px; overflow-y: auto; background: #f8f9fa; padding: 10px; border-radius: 4px;'>{}</pre>",
            pretty_data,
        )


@admin.register(SirenRecordImage)
class SirenRecordImageAdmin(admin.ModelAdmin):
    list_display = ("id", "record", "files_count")
    raw_id_fields = ("record",)

    @admin.display(description="Файлов")
    def files_count(self, obj):
        return obj.files.count()


@admin.register(SirenRecordSound)
class SirenRecordSoundAdmin(admin.ModelAdmin):
    list_display = ("id", "record", "files_count")
    raw_id_fields = ("record",)

    @admin.display(description="Файлов")
    def files_count(self, obj):
        return obj.files.count()
    
@admin.register(SirenAttempt)
class SirenAttemptAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "record", "status", "score", "created_at", "updated_at")
    list_filter = ("status", "record")
    search_fields = ("user__user__username", "record__slug", "record__title")
    readonly_fields = ("user", "record", "status", "score", "user_curve", "created_at", "updated_at")
    ordering = ("-created_at",)