import json
import re
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from roster.models.team import Season, Team, Card, Tag
from tg_bot.models import Bot


# ─── LEVEL MAPPING ──────────────────────────────────────────────
LEVEL_MAPPING = {
    "Мибблы от G до T": {"card_type": "text"},
    "Визуал от G до T": {"card_type": "visual"},
    "Миди от G до T": {"card_type": "text"},
    "Челлендж": {"card_type": "challenge"},
    "ББ: Макси": {"card_type": "bb_maxi"},
    "ББ: Иллюстрации": {"card_type": "bb_illustration"},
    "Мибблы от M до E": {"card_type": "text"},
    "Визуал от M до E": {"card_type": "visual"},
    "Миди от M до E": {"card_type": "text"},
    "Спецквест": {"card_type": "special"},
}


def strip_html(html: str) -> str:
    """Remove HTML tags and normalize whitespace."""
    if not html:
        return ""
    clean = re.sub(r'<[^>]+>', '', html)
    clean = re.sub(r'\s+', ' ', clean)
    return clean.strip()


def truncate_text(text: str, max_len: int = 800) -> str:
    """Truncate text to max_len and add ellipsis."""
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + "..."


def parse_words_to_size_tag(words_val) -> str:
    """Convert word count into rounded down thousands (e.g. 4800 -> 'size: 4k', 666 -> 'size: <1k')."""
    try:
        clean_val = str(words_val).replace(",", "").replace(" ", "").strip()
        words_int = int(clean_val)
    except (ValueError, TypeError):
        words_int = 0

    if words_int < 1000:
        return "size: <1k"

    thousands = words_int // 1000
    return f"size: {thousands}k"


class Command(BaseCommand):
    help = "Load AO3 works JSON into a gacha season"

    def add_arguments(self, parser):
        parser.add_argument("json_path", type=str, help="Path to AO3 works JSON export")
        parser.add_argument("--bot-id", type=int, default=1, help="Bot instance ID")
        parser.add_argument("--season-name", type=str, required=True, help="Display name of the season")
        parser.add_argument("--season-slug", type=str, required=True, help="URL-safe slug (used in /get_<slug>)")
        parser.add_argument("--end-date", type=str, default=None, help="Season end date YYYY-MM-DD")

    def handle(self, *args, **options):
        json_path = options["json_path"]
        bot_id = options["bot_id"]
        season_name = options["season_name"]
        season_slug = options["season_slug"]
        end_date_str = options["end_date"]

        # ─── LOAD JSON ────────────────────────────────────────
        with open(json_path, "r", encoding="utf-8") as f:
            works = json.load(f)

        # ─── GET BOT ──────────────────────────────────────────
        try:
            bot = Bot.objects.get(id=bot_id)
        except Bot.DoesNotExist:
            raise CommandError(f"Bot id={bot_id} not found")

        # ─── PARSE END DATE ───────────────────────────────────
        end_date = None
        if end_date_str:
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(
                tzinfo=timezone.get_current_timezone()
            )

        # ─── GET OR CREATE SEASON ─────────────────────────────
        season, created = Season.objects.get_or_create(
            slug=season_slug,
            defaults={
                "name": season_name,
                "start_date": timezone.now(),
                "end_date": end_date,
                "bot": bot,
            }
        )
        if not created:
            self.stdout.write(self.style.WARNING(
                f"Season '{season_name}' already exists, using existing"
            ))

        total_cards = 0
        skipped_cards = 0

        # ─── PROCESS EACH WORK ────────────────────────────────
        for idx, work in enumerate(works, 1):
            title = work.get("title", "").strip()
            if not title:
                self.stdout.write(self.style.WARNING(
                    f"  [{idx}] Skipping work without title"
                ))
                skipped_cards += 1
                continue

            summary_raw = work.get("summary", "")
            summary_clean = strip_html(summary_raw)
            summary_truncated = truncate_text(summary_clean, 800)

            level = work.get("level", "").strip()
            team_name = work.get("team", "Anonymous").strip()

            # ─── VALIDATE LEVEL ───────────────────────────────
            if level not in LEVEL_MAPPING:
                self.stdout.write(self.style.WARNING(
                    f"  [{idx}] Unknown level '{level}' for '{title}', skipping"
                ))
                skipped_cards += 1
                continue

            # ─── GET OR CREATE TEAM ───────────────────────────
            team, _ = Team.objects.get_or_create(
                season=season,
                name=team_name,
                defaults={"stars": 1}
            )

            # ─── CREATE CARD ──────────────────────────────────
            card, card_created = Card.objects.get_or_create(
                season=season,
                team=team,
                name=title,
                defaults={
                    "stars": 1,
                    "description": summary_truncated,  # Саммари пишется в деск
                }
            )

            if not card_created:
                self.stdout.write(self.style.WARNING(
                    f"  [{idx}] Skipping duplicate: '{title}'"
                ))
                skipped_cards += 1
                continue

            total_cards += 1

            # ─── PREPARE UNIQUE TAGS SET ──────────────────────
            raw_tags_set = set()

            # Системные мета-теги
            raw_tags_set.add(f"Level: {level}")
            raw_tags_set.add(f"Team: {team_name}")

            # Тег размера слов (например "size: 4k")
            size_tag = parse_words_to_size_tag(work.get("words", 0))
            raw_tags_set.add(size_tag)

            # Фэндом (разбираем если передана строка через запятую или одиночная)
            fandom_raw = work.get("fandom", "").strip()
            if fandom_raw:
                # Если фэндомы перечислены через запятую — разбиваем их
                for f_item in fandom_raw.split(","):
                    f_clean = f_item.strip()
                    if f_clean:
                        raw_tags_set.add(f_clean)

            # Список обычных тегов с AO3
            tags_list = work.get("tags", [])
            if isinstance(tags_list, list):
                for t in tags_list:
                    if t and str(t).strip():
                        raw_tags_set.add(str(t).strip())

            # ─── SAVE TAGS TO DATABASE ────────────────────────
            card_tags_to_add = []
            for tag_name in raw_tags_set:
                # Обрезаем под CharField max_length=255 у модели Tag
                truncated_tag_name = tag_name[:255]
                tag_obj, _ = Tag.objects.get_or_create(name=truncated_tag_name)
                card_tags_to_add.append(tag_obj)

            card.tags.set(card_tags_to_add)

            self.stdout.write(
                f"  [{idx}] + '{title}' | {level} | {team_name} | {size_tag} | Tags: {len(card_tags_to_add)}"
            )

        # ─── SUMMARY ──────────────────────────────────────────
        self.stdout.write(self.style.SUCCESS(
            f"\nDone: {total_cards} cards created, "
            f"{skipped_cards} skipped, "
            f"{season.teams.count()} teams, "
            f"in '{season.name}'"
        ))