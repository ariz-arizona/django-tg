# roster/admin.py
from django.contrib import admin

from tg_bot.admin import BotFileInline
from .models.team import Season, Team, Card
from .models.roll import UserRoll
from .models.tech import RollLimit


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
    inlines = [CardInline]

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


@admin.register(RollLimit)
class RollLimitAdmin(admin.ModelAdmin):
    list_display = ["limit_type", "bot", "value"]
    list_filter = ["bot", "limit_type"]
    search_fields = ["bot__name"]
