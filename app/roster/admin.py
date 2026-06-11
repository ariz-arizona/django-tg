# roster/admin.py
from django.contrib import admin
from .models import Season, Team, Card


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
    list_display = ['name', 'start_date', 'end_date', 'is_active', 'team_count']
    list_filter = ['is_active']
    search_fields = ['name']
    inlines = [TeamInline]

    def team_count(self, obj):
        return obj.teams.count()
    team_count.short_description = 'Команд'


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ['name', 'stars_display', 'season', 'card_count']
    list_filter = ['season', 'stars']
    search_fields = ['name']
    inlines = [CardInline]

    def stars_display(self, obj):
        return '⭐' * obj.stars
    stars_display.short_description = 'Звёздность'

    def card_count(self, obj):
        return obj.cards.count()
    card_count.short_description = 'Карт'


@admin.register(Card)
class CardAdmin(admin.ModelAdmin):
    list_display = ['name', 'stars_display', 'team', 'team_season']
    list_filter = ['team__season', 'team', 'stars']
    search_fields = ['name', 'description']

    def stars_display(self, obj):
        return '⭐' * obj.stars
    stars_display.short_description = 'Звёздность'

    def team_season(self, obj):
        return obj.team.season.name
    team_season.short_description = 'Сезон'