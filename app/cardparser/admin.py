from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe

from tg_bot.models import Bot, TgUser
from .models import ParseProduct, TgUserProduct, Brand, Category, ProductImage, BotSettings, EventCaption, ProductTemplate


@admin.register(TgUserProduct)
class TgUserProductAdmin(admin.ModelAdmin):
    list_display = ("tg_user", "product", "sent_at")
    list_filter = ("sent_at",)
    search_fields = ("tg_user__username", "product__caption")

# === Inline: Изображения в ParseProduct ===
class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 0
    readonly_fields = ("image_type", "file_id", "url", "created_at")
    fields = ("image_type", "file_id", "url", "created_at")
    can_delete = False
    show_change_link = True


# === Админка: TgUserProduct (Inline) ===
class TgUserProductInline(admin.TabularInline):
    model = TgUserProduct
    extra = 0
    readonly_fields = ("sent_at", "tg_user")
    fields = ("tg_user", "sent_at")
    can_delete = False  # чтобы случайно не удалили историю
    show_change_link = True


# === Админка: ParseProduct ===
@admin.register(ParseProduct)
class ParseProductAdmin(admin.ModelAdmin):
    list_display = (
        "product_id",
        "product_type",
        "name",
        "brand__name",
        "category__name",
        "created_at",
        "updated_at",
    )
    list_filter = ("product_type", "brand", "category", "created_at")
    search_fields = ("product_id", "caption", "brand__name", "category__name", "name")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("brand", "category")  # удобно при большом количестве
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

    fieldsets = (
        (
            "Основное",
            {
                "fields": ("product_id", "product_type", "name", "caption"),
            },
        ),
        (
            "Связи",
            {
                "fields": ("brand", "category"),
            },
        ),
        (
            "Аудит",
            {
                "fields": ("created_at", "updated_at"),
            },
        ),
    )

    inlines = [ProductImageInline, TgUserProductInline]


# === Админка: ProductImage (опционально отдельно) ===
@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    list_display = (
        "product",
        "image_type",
        "file_id_preview",
        "url_preview",
        "created_at",
    )
    list_filter = ("image_type", "created_at")
    search_fields = ("file_id", "url", "product__product_id")
    readonly_fields = ("created_at", "updated_at", "file_id_preview", "url_preview")

    def file_id_preview(self, obj):
        if obj.file_id:
            return format_html(
                '<span title="{}">{}</span>', obj.file_id, obj.file_id[:20] + "..."
            )
        return "-"

    file_id_preview.short_description = "file_id"

    def url_preview(self, obj):
        if obj.url:
            return format_html('<a href="{0}" target="_blank">{0}</a>', obj.url)
        return "-"

    url_preview.short_description = "URL"


# === Админка: Brand ===
@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "brand_id",
        "product_type",
        "products_count",
        "created_at",
    )
    list_filter = ("product_type", "created_at")
    search_fields = ("name", "brand_id")
    readonly_fields = ("created_at",)
    fieldsets = (
        (
            "Основная информация",
            {
                "fields": ("name", "brand_id", "product_type"),
            },
        ),
        (
            "Аудит",
            {
                "fields": ("created_at",),
            },
        ),
    )
    ordering = ("name",)

    def products_count(self, obj):
        """Отображает количество товаров этого бренда"""
        count = obj.parseproduct_set.count()
        url = (
            f"/admin/parser/parseproduct/?brand__id={obj.id}"
            if hasattr(obj, "parseproduct_set")
            else "#"
        )
        return format_html(
            '<a href="{}" style="text-decoration: none;">🧩 <strong>{}</strong></a>',
            url,
            count,
        )

    products_count.short_description = "Товары"
    products_count.allow_tags = True


# === Админка: Category ===
@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "subject_id",
        "product_type",
        "products_count",
        "created_at",
    )
    list_filter = ("product_type", "created_at")
    search_fields = ("name", "subject_id")
    readonly_fields = ("created_at",)
    fieldsets = (
        (
            "Категория",
            {
                "fields": ("name", "subject_id", "product_type"),
            },
        ),
        (
            "Аудит",
            {
                "fields": ("created_at",),
            },
        ),
    )
    ordering = ("name",)

    def products_count(self, obj):
        """Количество товаров в категории"""
        count = obj.parseproduct_set.count()
        url = f"/admin/parser/parseproduct/?category__id={obj.id}"
        return format_html(
            '<a href="{}" style="text-decoration: none;">📦 <strong>{}</strong></a>',
            url,
            count,
        )

    products_count.short_description = "Товары"
    products_count.allow_tags = True

@admin.register(BotSettings)
class BotSettingsAdmin(admin.ModelAdmin):
    # Поля, отображаемые в списке
    list_display = [
        "active_status",
        "marketing_group_id",
        "picture_chat_id",
        "parser_urls",
        "updated_at",
    ]

    # Только для чтения в форме
    readonly_fields = ["created_at", "updated_at"]
    
    # Группировка полей в форме
    fieldsets = [
        ("Активация", {
            "fields": ["active"],
            "description": "⚠️ Только одна запись может быть активной."
        }),
        ("Основные настройки", {
            "fields": ["marketing_group_id", "picture_chat_id"]
        }),
        ("URL парсеров", {
            "fields": ["parser_url_ozon", "parser_url_wb"]
        }),
        ("Аудит", {
            "fields": ["created_at", "updated_at"],
            "classes": ["collapse"]
        }),
    ]

    # Сортировка
    ordering = ["-active", "-updated_at"]

    # Поиск
    search_fields = ["marketing_group_id", "picture_chat_id"]

    # Фильтры
    list_filter = ["active"]

    @admin.display(description="Статус")
    def active_status(self, obj):
        return format_html(
            '<span style="color: {};">●</span> {}',
            "green" if obj.active else "gray",
            "Активно" if obj.active else "Неактивно"
        )

    @admin.display(description="Парсеры")
    def parser_urls(self, obj):
        lines = []
        if obj.parser_url_ozon:
            lines.append(f"Ozon: <code style='font-size:0.9em'>{obj.parser_url_ozon}</code>")
        if obj.parser_url_wb:
            lines.append(f"WB: <code style='font-size:0.9em'>{obj.parser_url_wb}</code>")
        return format_html("<br>".join(lines)) if lines else "-"
    
@admin.register(EventCaption)
class EventCaptionAdmin(admin.ModelAdmin):
    # Поля, которые видно в списке
    list_display = (
        'get_event_type_display_name',
        'short_text',
        'short_caption',
        'is_active',
        'updated_at'
    )

    # Поля, которые можно редактировать прямо в списке
    list_editable = ('is_active',)

    # Фильтры справа
    list_filter = ('event_type', 'is_active')

    # Поиск по тексту подписи
    search_fields = ('caption',)

    # Сортировка: сначала по типу, потом активные сверху
    ordering = ('event_type', '-is_active')

    # Чтобы поле updated_at было только для чтения
    readonly_fields = ('updated_at',)

    # Удобное отображение типа события
    def get_event_type_display_name(self, obj):
        return obj.get_event_type_display()
    get_event_type_display_name.short_description = "Тип события"
    get_event_type_display_name.admin_order_field = 'event_type'

    # Превью подписи
    def short_text(self, obj):
        if not obj.text:
            return "-"
        return (obj.text.strip()[:60] + "...") if len(obj.text.strip()) > 60 else obj.text.strip()
    short_text.short_description = "Текст выдачи"

    def short_caption(self, obj):
        if not obj.caption:
            return "-"
        return (obj.caption.strip()[:60] + "...") if len(obj.caption.strip()) > 60 else obj.caption.strip()
    short_caption.short_description = "Подпись к фото"
    
@admin.register(ProductTemplate)
class ProductTemplateAdmin(admin.ModelAdmin):
    list_display = ["name", "is_default", "created_at", "updated_at"]
    list_filter = ["is_default"]
    search_fields = ["name"]
    ordering = ["name"]
    
    fieldsets = (
        (None, {
            "fields": ("name", "is_default", "template"),
            "description": (
                "<p><strong>Плейсхолдеры:</strong> {brand}, {name}, {price_display}, "
                "{sizes_display}, {availability}, {link}, {sku}</p>"
            )
        }),
        ("Системная информация", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )

    readonly_fields = ["created_at", "updated_at"]

    def get_readonly_fields(self, request, obj=None):
        # Запрещаем менять название после создания (чтобы не сломать код, который на него ссылается)
        if obj:
            return self.readonly_fields + ["name"]
        return self.readonly_fields