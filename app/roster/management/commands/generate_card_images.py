# roster/management/commands/generate_card_images.py
"""
Generate card images for AO3 works using Pillow canvas.

For cards without images, creates a 4:3 vertical canvas with:
- Full card: pixel semi-transparent black stripes (length-tier pattern)
- Middle: title + summary
- Bottom: centered rounded tag squares with full tag names

Usage:
    python manage.py generate_card_images \
        --season-slug fk_summer_2026 \
        --bot-id 1 \
        --chat-id 123456789 \
        --dry-run
"""

import hashlib
import os
import textwrap
from io import BytesIO

import requests
from django.core.management.base import BaseCommand
from django.db.models import Count
from PIL import Image, ImageDraw, ImageFont

from roster.models.team import Card, Season
from tg_bot.models import Bot, BotFile


# ─── CONFIG ─────────────────────────────────────────────────────
WIDTH = 600
HEIGHT = 800

# Фон карточки = рейтинг контента (чтобы сразу было видно, читаешь ты это или нет).
# Приглушённая тёмная палитра — единый визуальный ряд, рейтинг считывается по оттенку,
# а не по яркости всей карты.
RATING_COLORS = {
    "Not Rated": "#3A3A3A",
    "General Audiences": "#1F3D2B",       # тёмно-зелёный
    "Teen And Up Audiences": "#4A3B14",   # тёмно-охра
    "Mature": "#4A2A10",                  # тёмно-оранжевый / коричневый
    "Explicit": "#4A1620",                # тёмно-бордовый
}

# Font URLs - Google Fonts CDN (supports Cyrillic)
FONT_URL = "https://github.com/googlefonts/opensans/raw/main/fonts/ttf/OpenSans-Regular.ttf"
FONT_BOLD_URL = "https://github.com/googlefonts/opensans/raw/main/fonts/ttf/OpenSans-Bold.ttf"
FONT_DIR = "/tmp/fonts"


def _ensure_fonts():
    """Download Open Sans fonts with Cyrillic support."""
    os.makedirs(FONT_DIR, exist_ok=True)
    fonts = {
        "regular": (FONT_URL, f"{FONT_DIR}/OpenSans-Regular.ttf"),
        "bold": (FONT_BOLD_URL, f"{FONT_DIR}/OpenSans-Bold.ttf"),
    }
    for name, (url, path) in fonts.items():
        if not os.path.exists(path):
            try:
                resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                with open(path, "wb") as f:
                    f.write(resp.content)
            except Exception as e:
                print(f"Font download failed ({name}): {e}")
                return None, None
    return fonts["regular"][1], fonts["bold"][1]


def get_bg_color(rating: str) -> str:
    return RATING_COLORS.get(rating, "#2E2E2E")


def get_stripe_style(words: int) -> tuple[str, int, bool]:
    """
    Определяет паттерн полосок по длине текста.
    Три РАЗНЫХ узора, а не один и тот же паттерн с разным шагом:
      - mini: редкие диагональные полоски
      - midi: частые диагональные полоски
      - maxi: крестики (диагонали в обе стороны)
    Возвращает (ключ, шаг между полосками, нужен ли crosshatch).
    """
    if words < 4000:
        return "mini", 24, False
    elif words < 15000:
        return "midi", 10, False
    else:
        return "maxi", 14, True


def tag_to_color(tag_name: str) -> str:
    h = hashlib.md5(tag_name.encode("utf-8")).hexdigest()
    return f"#{h[:6]}"


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def brightness(hex_color: str) -> float:
    r, g, b = hex_to_rgb(hex_color)
    return 0.299 * r + 0.587 * g + 0.114 * b


def truncate_text(text: str, max_len: int = 100) -> str:
    """Обрезает текст до max_len и добавляет троеточие."""
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + "…"


def decline_words(n: int) -> str:
    """
    Склонение слова "слово" для русского языка.
    1 слово, 2-4 слова, 5-20 слов, 21 слово, 22-24 слова и т.д.
    """
    n = abs(n)
    last_two = n % 100
    last_one = n % 10

    if 11 <= last_two <= 19:
        return f"{n:,} слов".replace(",", " ")
    elif last_one == 1:
        return f"{n:,} слово".replace(",", " ")
    elif 2 <= last_one <= 4:
        return f"{n:,} слова".replace(",", " ")
    else:
        return f"{n:,} слов".replace(",", " ")


def draw_stripes(draw, width, height, color, spacing, stripe_width=2, cross=False):
    """
    Рисует диагональные полоски "\" по всей карте.
    Если cross=True — дополнительно рисует полоски "/" поверх,
    получается сетка-крестик (для maxi-тира).
    """
    start_offset = -height
    end_offset = width

    for offset in range(start_offset, end_offset, spacing):
        draw.line([(offset, 0), (offset + height, height)], fill=color, width=stripe_width)

    if cross:
        for offset in range(start_offset, end_offset, spacing):
            draw.line([(offset + height, 0), (offset, height)], fill=color, width=stripe_width)


def generate_card_image(title: str, summary: str, rating: str, words: int, tags: list[str],
                        font_regular: str, font_bold: str) -> Image.Image:
    bg_color = get_bg_color(rating)
    stripe_key, stripe_spacing, cross = get_stripe_style(words)

    img = Image.new("RGBA", (WIDTH, HEIGHT), bg_color)
    draw = ImageDraw.Draw(img)

    # ─── FULL CARD: ЗАТЕМНЕНИЕ ФОНА ─────────────────────────────
    bg_r, bg_g, bg_b = hex_to_rgb(bg_color)
    dark_factor = 0.6
    dark_r, dark_g, dark_b = int(bg_r * dark_factor), int(bg_g * dark_factor), int(bg_b * dark_factor)
    dark_line_color = (dark_r, dark_g, dark_b)

    draw_stripes(draw, WIDTH, HEIGHT, dark_line_color, stripe_spacing, cross=cross)

    # ─── MIDDLE: Title + Summary ────────────────────────────────
    top_y = HEIGHT // 4
    y = top_y

    try:
        font_title = ImageFont.truetype(font_bold, 36) if font_bold else ImageFont.load_default()
        font_summary = ImageFont.truetype(font_regular, 20) if font_regular else ImageFont.load_default()
        font_tag = ImageFont.truetype(font_regular, 16) if font_regular else ImageFont.load_default()
    except OSError:
        font_title = font_summary = font_tag = ImageFont.load_default()

    # Title
    title_lines = textwrap.wrap(title, width=22)
    for line in title_lines[:3]:
        bbox = draw.textbbox((0, 0), line, font=font_title)
        tw = bbox[2] - bbox[0]
        x = (WIDTH - tw) // 2
        draw.text((x + 2, y + 2), line, fill=(0, 0, 0, 120), font=font_title)
        draw.text((x, y), line, fill="white", font=font_title)
        y += 48

    # Words count (под заголовком, до саммари) — со склонением
    words_text = decline_words(words)
    bbox = draw.textbbox((0, 0), words_text, font=font_summary)
    tw = bbox[2] - bbox[0]
    x = (WIDTH - tw) // 2
    draw.text((x, y), words_text, fill="#CFCFCF", font=font_summary)
    y += 34

    y += 10

    # Summary (Centered)
    summary_clean = summary.replace("\n", " ").strip()
    summary_lines = textwrap.wrap(summary_clean, width=32)
    for line in summary_lines[:4]:
        bbox = draw.textbbox((0, 0), line, font=font_summary)
        tw = bbox[2] - bbox[0]
        x = (WIDTH - tw) // 2
        draw.text((x, y), line, fill="#F5F5F5", font=font_summary)
        y += 28

    # ─── BOTTOM: Centered full-text tags (HALF CARD) ────────────
    tag_area_top = HEIGHT // 2 + 30  # чуть больше отступа от саммари до тегов

    # Создаем список кортежей (текст, цвет, ширина_текста, ширина_бокса)
    tag_elements = []

    for tag_name in tags[:18]:  # Берем топ-18 тегов
        # Временная функция для проверки влезания текста
        def get_fitted_text(text, max_px_width):
            for i in range(len(text), 0, -1):
                test_text = text[:i] + ("…" if i < len(text) else "")
                bbox = draw.textbbox((0, 0), test_text, font=font_tag)
                if bbox[2] - bbox[0] <= max_px_width:
                    return test_text
            return text[:1] + "…"

        color = tag_to_color(tag_name)

        # УВЕЛИЧИЛИ МАКСИМАЛЬНУЮ ШИРИНУ ПЛАШКИ (с 140 до 180 пикселей)
        max_allowed_width = 180
        box_height = 36

        # Внутренние отступы текста от краев плашки (с 12 до 14)
        padding = 14
        max_text_width = max_allowed_width - padding * 2

        # Получаем красиво обрезанный текст с троеточием по ширине
        display_text = get_fitted_text(tag_name, max_text_width)
        bbox = draw.textbbox((0, 0), display_text, font=font_tag)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]  # Высота текста для вертикального центрирования

        # Финальная ширина квадрата зависит от текста, но не больше максимума
        box_width = max(44, min(text_width + padding * 2, max_allowed_width))

        # Сохраняем всё, включая высоту текста
        tag_elements.append((display_text, color, box_width, box_height, text_width, text_height))

    # ─── ОТРИСОВКА И ЦЕНТРИРОВАНИЕ СТРОК ────────────────────────
    gap = 10
    margin = 20

    x = margin
    y = tag_area_top

    # Список для хранения элементов текущей строки, чтобы центрировать её
    current_row_elements = []
    current_row_width = 0

    for i, (text, color, box_w, box_h, text_w, text_h) in enumerate(tag_elements):
        # Если текущий элемент не влезает в строку
        if x + box_w + gap > WIDTH - margin and current_row_elements:
            # --- ЦЕНТРИРУЕМ ТЕКУЩУЮ СТРОКУ ---
            offset_x = (WIDTH - margin * 2 - current_row_width) // 2
            for elem_data in current_row_elements:
                # Перерисовываем элемент со смещением offset_x
                ex, ey, etext, ecolor, ebox_w, ebox_h, etext_w, etext_h = elem_data
                new_ex = ex + offset_x

                # Рисуем квадрат
                r, g, b = hex_to_rgb(ecolor)
                tag_img = Image.new("RGBA", (ebox_w, ebox_h), (0, 0, 0, 0))
                tag_draw = ImageDraw.Draw(tag_img)
                tag_draw.rounded_rectangle([0, 0, ebox_w - 1, ebox_h - 1], radius=10,
                                           fill=(r, g, b, 230))
                tag_draw.rounded_rectangle([0, 0, ebox_w - 1, ebox_h - 1], radius=10,
                                           outline=(0, 0, 0, 80), width=2)
                img.paste(tag_img, (new_ex, ey), tag_img)

                # --- ЦЕНТРИРОВАНИЕ ЧЕРЕЗ anchor="mm" (по центру бокса и по x, и по y) ---
                text_color = "white" if brightness(ecolor) < 140 else "black"
                center_x = new_ex + ebox_w // 2
                center_y = ey + ebox_h // 2
                draw.text((center_x, center_y), etext, fill=text_color, font=font_tag, anchor="mm")

            # --- СБРОС СТРОКИ ---
            current_row_elements = []
            current_row_width = 0
            x = margin
            y += box_h + gap

        # Добавляем элемент в текущую строку
        current_row_elements.append((x, y, text, color, box_w, box_h, text_w, text_h))
        current_row_width += box_w + (gap if current_row_elements else 0)
        x += box_w + gap

    # --- ОТРИСОВКА ПОСЛЕДНЕЙ СТРОКИ (если она есть) ---
    if current_row_elements:
        offset_x = (WIDTH - margin * 2 - current_row_width) // 2
        for elem_data in current_row_elements:
            ex, ey, etext, ecolor, ebox_w, ebox_h, etext_w, etext_h = elem_data
            new_ex = ex + offset_x

            r, g, b = hex_to_rgb(ecolor)
            tag_img = Image.new("RGBA", (ebox_w, ebox_h), (0, 0, 0, 0))
            tag_draw = ImageDraw.Draw(tag_img)
            tag_draw.rounded_rectangle([0, 0, ebox_w - 1, ebox_h - 1], radius=10,
                                       fill=(r, g, b, 230))
            tag_draw.rounded_rectangle([0, 0, ebox_w - 1, ebox_h - 1], radius=10,
                                       outline=(0, 0, 0, 80), width=2)
            img.paste(tag_img, (new_ex, ey), tag_img)

            text_color = "white" if brightness(ecolor) < 140 else "black"
            center_x = new_ex + ebox_w // 2
            center_y = ey + ebox_h // 2
            draw.text((center_x, center_y), etext, fill=text_color, font=font_tag, anchor="mm")

    return img.convert("RGB")


class Command(BaseCommand):
    help = "Generate card images for cards without images in a season"

    def add_arguments(self, parser):
        parser.add_argument("--season-slug", type=str, required=True)
        parser.add_argument("--bot-id", type=int, required=True)
        parser.add_argument("--chat-id", type=str, required=True)
        parser.add_argument("--dry-run", action="store_true", help="Save to /tmp, don't upload")

    def handle(self, *args, **options):
        season_slug = options["season_slug"]
        bot_id = options["bot_id"]
        chat_id = options["chat_id"]
        dry_run = options["dry_run"]

        self.stdout.write("Downloading fonts...")
        font_regular, font_bold = _ensure_fonts()
        if not font_regular:
            self.stdout.write(self.style.WARNING("Using default fonts (Cyrillic may not work)"))

        try:
            season = Season.objects.get(slug=season_slug)
            bot = Bot.objects.get(id=bot_id, is_enabled=True)
        except Season.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Season '{season_slug}' not found"))
            return
        except Bot.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"Bot {bot_id} not found"))
            return

        # ИЗМЕНЕНИЕ: убрали prefetch_related("image"), чтобы аннотация работала корректно
        cards = []
        for card in Card.objects.filter(season=season):
            if not card.image.filter(bot=bot).exists():
                cards.append(card)

        self.stdout.write(f"Found {len(cards)} cards without images")

        success = 0
        for card in cards:
            words = 5000
            for line in card.description.split("\n"):
                if line.startswith("Words:"):
                    try:
                        words = int(line.replace("Words:", "").strip().replace(",", ""))
                    except ValueError:
                        pass
                    break

            rating = "Not Rated"
            for tag in card.tags.filter(name__startswith="Rating: "):
                rating = tag.name.replace("Rating: ", "")
                break

            # ─── СОРТИРОВКА ТЕГОВ ──────────────────────────
            sorted_tags = card.tags.exclude(
                name__startswith=("Category:", "Rating:", "Size:", "Warning:", "Ship:", "Character:")
            ).order_by("name").distinct()

            # Собираем данные для генератора: просто список строк
            tags = [tag.name for tag in sorted_tags[:18]]

            summary = card.description.split("\n")[-1] if "\n" in card.description else card.description

            img = generate_card_image(title=card.name, summary=summary, rating=rating,
                                      words=words, tags=tags,
                                      font_regular=font_regular, font_bold=font_bold)

            if dry_run:
                path = f"/tmp/card_{card.id}.png"
                img.save(path)
                self.stdout.write(f"  💾 {path}")
                success += 1
                continue

            buffer = BytesIO()
            img.save(buffer, format="PNG")
            buffer.seek(0)

            file_id = self._upload(bot.token, chat_id, buffer, f"card_{card.id}.png")
            if file_id:
                from django.contrib.contenttypes.models import ContentType
                ct = ContentType.objects.get_for_model(Card)
                BotFile.objects.update_or_create(
                    content_type=ct, object_id=card.id, bot=bot, field_name="image",
                    defaults={"file_id": file_id},
                )
                self.stdout.write(self.style.SUCCESS(f"  ✅ {card.name}"))
                success += 1
            else:
                self.stderr.write(self.style.ERROR(f"  ❌ {card.name}"))

        self.stdout.write(self.style.SUCCESS(f"\nDone: {success}/{len(cards)}"))

    def _upload(self, token: str, chat_id: str, buffer: BytesIO, filename: str) -> str | None:
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        try:
            resp = requests.post(url, data={"chat_id": chat_id},
                                 files={"photo": (filename, buffer, "image/png")}, timeout=30)
            data = resp.json()
            if not data.get("ok"):
                self.stderr.write(f"    API: {data.get('description')}")
                return None
            photo = data["result"].get("photo", [])
            return max(photo, key=lambda p: p.get("file_size", 0)).get("file_id") if photo else None
        except Exception as e:
            self.stderr.write(f"    Error: {e}")
            return None