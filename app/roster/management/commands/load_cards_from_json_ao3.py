"""
Load AO3 works JSON into a gacha season.

Expected JSON structure (one object per work, array of works):
[
  {
    "title":       "Work Title",
    "url":         "https://archiveofourown.org/works/12345678",
    "authors":     ["fandom Team Name 2026 (slug)", "RealAuthorName"],
    "fandoms":     ["Fandom Name"],
    "rating":      "Teen And Up Audiences",
    "warnings":    "No Archive Warnings Apply",
    "category":    "Gen",
    "status":      "Complete Work",
    "tags": {
      "warnings":      ["No Archive Warnings Apply"],
      "relationships": ["Character A/Character B"],
      "characters":    ["Character A", "Character B"],
      "freeforms":     ["Fix-It", "Angst", "Fandom Kombat 2026", ...]
    },
    "tags_flat":   ["No Archive Warnings Apply", "Character A/Character B", ...],
    "words":       "1,234",
    "language":    "Русский",
    "chapters":    "1/1",
    "hits":        "42",
    "summary":     "<p>HTML summary text</p>",
    "ts":          1234567890123
  }
]

Logic:
- Team name is extracted from the author entry starting with "fandom " (case-insensitive).
  If no such entry exists, team falls back to "Anonymous".
- Real author name is the first non-fandom entry in authors[].
- Summary HTML is stripped and truncated to 800 chars in the card description.
- Only freeform tags are checked against stop-patterns. Warnings, relationships
  and characters are kept as-is (they are structural AO3 metadata, not user noise).

Usage:
    python manage.py load_cards_from_json_ao3 works.json \
        --season-name "Fandom Kombat Summer 2026" \
        --season-slug fk_summer_2026 \
        --bot-id 1 \
        --end-date 2026-09-01
"""

import json
import re
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from roster.models.team import Season, Team, Card, Tag
from tg_bot.models import Bot


# ─── STOP WORDS: only freeform noise (contest markers, copyright, etc.) ──
STOP_PATTERNS = [
    r'fandom', r'фандом', r'команда', r'team', r'битва', r'kombat', r'combat', r'конкурс',
    r'фк\d+', r'fk\d+', r'мибблы', r'mibbles',
    r'don[\'\']?\s*copy', r'don[\'\']?\s*steal', r'украден', r'не\s*на\s*аоз',
    r'ao3', r'if\s*you\s*see\s*this',
    r'single\s*work', r'freeform', r'no\s*archive\s*warnings', r'not\s*for\s*vote',
    r'ai-generated', r'generative',
]

STOP_REGEX = re.compile(
    r'(' + r'|'.join(f'(?:{p})' for p in STOP_PATTERNS) + r')',
    re.IGNORECASE
)


def is_technical_tag(tag_name: str) -> bool:
    return bool(STOP_REGEX.search(tag_name))


def parse_word_count(words_str: str) -> int:
    return int(words_str.replace(",", ""))


def get_size_bucket(words: int) -> str:
    if words < 4000:
        return "до 4к"
    elif words < 15000:
        return "4–15к"
    else:
        return "больше 15к"


def strip_html(html: str) -> str:
    if not html:
        return ""
    clean = re.sub(r'<[^>]+>', '', html)
    clean = re.sub(r'\s+', ' ', clean)
    return clean.strip()


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

        with open(json_path, "r", encoding="utf-8") as f:
            works = json.load(f)

        try:
            bot = Bot.objects.get(id=bot_id)
        except Bot.DoesNotExist:
            raise CommandError(f"Bot id={bot_id} not found")

        end_date = None
        if end_date_str:
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").replace(
                tzinfo=timezone.get_current_timezone()
            )

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
            self.stdout.write(self.style.WARNING(f"Season '{season_name}' already exists, using existing"))

        total_cards = 0
        total_tags_kept = 0

        FANDOM_REGEX = re.compile(r'^fandom\s+', re.IGNORECASE)

        for work in works:
            authors = work.get("authors", [])

            # Team name from fandom entry
            fandom_entry = None
            for author in authors:
                if FANDOM_REGEX.match(author):
                    fandom_entry = author
                    break

            if fandom_entry:
                fandom_name = re.sub(r'^fandom\s+', '', fandom_entry, flags=re.IGNORECASE)
                fandom_name = re.sub(r'\s*\([^)]+\)$', '', fandom_name).strip()
            else:
                fandom_name = "Anonymous"

            # Real author for description
            author_name = "Anonymous"
            for a in authors:
                if not FANDOM_REGEX.match(a):
                    author_name = a
                    break

            team, _ = Team.objects.get_or_create(
                season=season,
                name=fandom_name,
                defaults={"stars": 1}
            )

            # Build description with summary
            summary_clean = strip_html(work.get("summary", ""))
            description_parts = [
                f"Author: {author_name}",
                f"Words: {work['words']}",
                f"Chapters: {work['chapters']}",
                f"Hits: {work['hits']}",
            ]
            if summary_clean:
                summary_truncated = summary_clean[:800]
                if len(summary_clean) > 800:
                    summary_truncated += "..."
                description_parts.append(f"\n{summary_truncated}")

            card, created = Card.objects.get_or_create(
                season=season,
                team=team,
                name=work["title"],
                defaults={
                    "stars": 1,
                    "description": "\n".join(description_parts),
                }
            )

            if not created:
                self.stdout.write(self.style.WARNING(f"  Skipping duplicate: {work['title']}"))
                continue

            total_cards += 1

            # System tags: category, rating, size bucket
            cat_tag, _ = Tag.objects.get_or_create(name=f"Category: {work['category']}")
            card.tags.add(cat_tag)

            rating_tag, _ = Tag.objects.get_or_create(name=f"Rating: {work['rating']}")
            card.tags.add(rating_tag)

            words = parse_word_count(work["words"])
            size_tag, _ = Tag.objects.get_or_create(name=f"Size: {get_size_bucket(words)}")
            card.tags.add(size_tag)

            # ─── AO3 structured tags ──────────────────────────
            tags_data = work.get("tags", {})

            # Warnings → prefix "Warning: "
            for tag_name in tags_data.get("warnings", []):
                tag, _ = Tag.objects.get_or_create(name=f"Warning: {tag_name}")
                card.tags.add(tag)

            # Relationships → prefix "Ship: "
            for tag_name in tags_data.get("relationships", []):
                tag, _ = Tag.objects.get_or_create(name=f"Ship: {tag_name}")
                card.tags.add(tag)

            # Characters → prefix "Character: "
            for tag_name in tags_data.get("characters", []):
                tag, _ = Tag.objects.get_or_create(name=f"Character: {tag_name}")
                card.tags.add(tag)

            # Freeforms → filter technical, no prefix
            kept_count = 0
            for tag_name in tags_data.get("freeforms", []):
                if is_technical_tag(tag_name):
                    continue
                tag, _ = Tag.objects.get_or_create(name=tag_name)
                card.tags.add(tag)
                kept_count += 1

            total_tags_kept += kept_count
            self.stdout.write(f"  + {work['title']} → {fandom_name} ({kept_count} freeform tags)")

        self.stdout.write(self.style.SUCCESS(
            f"\nDone: {total_cards} cards, {season.teams.count()} teams, "
            f"{total_tags_kept} freeform tags kept in '{season.name}'"
        ))