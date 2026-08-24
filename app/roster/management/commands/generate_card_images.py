# roster/management/commands/generate_card_images.py
"""
Generate card images for AO3 works using Pillow canvas.

For cards without images, creates a 600x600 canvas with:
- Full card: pixel semi-transparent black stripes (length-tier pattern)
- Middle: title + summary

Usage:
    python manage.py generate_card_images \\
        --season-slug fk_summer_2026 \\
        --bot-id 1 \\
        --chat-id 123456789 \\
        --dry-run
"""

import unicodedata
import os
import textwrap
from io import BytesIO

import numpy as np
import requests
from django.core.management.base import BaseCommand
from django.db.models import Count
from PIL import Image, ImageDraw, ImageFont

from roster.models.team import Card, Season
from tg_bot.models import Bot, BotFile


# ─── CONFIG ─────────────────────────────────────────────────────
WIDTH = 600
HEIGHT = 600

# Градиентные цвета рамок по уровню рейтинга
RATING_BORDER_GRADIENTS = {
    "Not Rated": {"start": "#0400FF", "end": "#FF00F2"},
    "G-T": {"start": "#FFFB00", "end": "#8BC34A"},
    "M-E": {"start": "#B71C1C", "end": "#FF9800"},
}

# Цвета для спецквеста и челленджа
SPECIAL_BORDER_COLOR = "#000000"    # чёрный для спецквеста
CHALLENGE_BORDER_COLOR = "#FFFFFF"  # белый для челленджа

# Фоны
BG_TEXT = (245, 240, 232, 255)      # очень светло-бежевый
BG_VISUAL = (45, 45, 45, 255)       # тёмный

# Font URLs - Google Fonts CDN (supports Cyrillic)
FONT_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/alegreyasans/AlegreyaSans-Regular.ttf"
FONT_BOLD_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/alegreyasans/AlegreyaSans-ExtraBold.ttf"
FONT_DIR = "/tmp/fonts"

# ─── FONT HELPERS ───────────────────────────────────────────────

def _ensure_fonts():
    """Download Noto Sans fonts with Cyrillic support."""
    os.makedirs(FONT_DIR, exist_ok=True)
    fonts = {
        "regular": (FONT_URL, f"{FONT_DIR}/NotoSans-Regular.ttf"),
        "bold": (FONT_BOLD_URL, f"{FONT_DIR}/NotoSans-Bold.ttf"),
    }
    for name, (url, path) in fonts.items():
        # Если файл не существует или пустой (меньше 1 КБ) — перескачиваем
        if not os.path.exists(path) or os.path.getsize(path) < 1024:
            try:
                resp = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                with open(path, "wb") as f:
                    f.write(resp.content)
            except Exception as e:
                print(f"Font download failed ({name}): {e}")
                return None, None
    return fonts["regular"][1], fonts["bold"][1]


# ─── COLOR / BORDER HELPERS ───────────────────────────────────

def get_border_gradient(rating: str, card_format: str) -> dict:
    """
    Возвращает градиент рамки.
    Для спецквеста - чёрный, для челленджа - белый,
    для остальных - зависит от рейтинга.
    """
    if card_format == "special":
        return {"start": SPECIAL_BORDER_COLOR, "end": SPECIAL_BORDER_COLOR}
    elif card_format == "challenge":
        return {"start": CHALLENGE_BORDER_COLOR, "end": CHALLENGE_BORDER_COLOR}
    else:
        return RATING_BORDER_GRADIENTS.get(rating, RATING_BORDER_GRADIENTS["Not Rated"])


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def mix_rgb(color_a: str, color_b: str, t: float = 0.5) -> tuple[int, int, int]:
    """Смешивает два hex-цвета в заданной пропорции (для тени текста)."""
    ra, ga, ba = hex_to_rgb(color_a)
    rb, gb, bb = hex_to_rgb(color_b)
    return (
        int(ra * (1 - t) + rb * t),
        int(ga * (1 - t) + gb * t),
        int(ba * (1 - t) + bb * t),
    )


# ─── STRIPE HELPERS ─────────────────────────────────────────────

def get_stripe_style(words: int) -> tuple[str, int, bool]:
    """
    Определяет паттерн полосок по длине текста.
    Три РАЗНЫХ узора:
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


# ─── GRADIENT / FRAME HELPERS ───────────────────────────────────

def make_diagonal_gradient(width: int, height: int, color_start: str, color_end: str) -> Image.Image:
    """
    Строит сплошной диагональный градиент (top-left -> bottom-right)
    на весь холст. Используется вместе с маской формы рамки, чтобы
    рамка была залита ОДНИМ непрерывным градиентом, а не рассыпалась
    на разноцветные куски по углам, как это было при поэдже-заливке
    каждой стороны рамки отдельным линейным градиентом.
    """
    rgb_start = np.array(hex_to_rgb(color_start), dtype=np.float64)
    rgb_end = np.array(hex_to_rgb(color_end), dtype=np.float64)

    xx, yy = np.meshgrid(np.arange(width), np.arange(height))
    denom = max(width + height - 2, 1)
    t = (xx + yy) / denom
    t = np.clip(t, 0.0, 1.0)[..., None]  # shape (h, w, 1) for broadcasting

    rgb = rgb_start * (1 - t) + rgb_end * t
    rgba = np.dstack([rgb, np.full((height, width), 255.0)]).astype(np.uint8)
    return Image.fromarray(rgba, mode="RGBA")


def build_frame_mask(width: int, height: int, margin: int, thickness: int,
                      style: str = "single", gap: int = 4) -> Image.Image:
    """
    Строит L-маску формы рамки (single/double/double_thick/dashed/dotted).
    Единая маска затем используется, чтобы вырезать нужную форму из
    сплошного диагонального градиента — это гарантирует, что рамка
    закрашена одним цельным градиентом без разрывов на стыках/углах.
    """
    mask = Image.new("L", (width, height), 0)
    mdraw = ImageDraw.Draw(mask)

    def draw_solid_ring(m, th):
        mdraw.rectangle([m, m, width - m, m + th], fill=255)                 # top
        mdraw.rectangle([m, height - m - th, width - m, height - m], fill=255)  # bottom
        mdraw.rectangle([m, m, m + th, height - m], fill=255)                # left
        mdraw.rectangle([width - m - th, m, width - m, height - m], fill=255)  # right

    if style == "single":
        draw_solid_ring(margin, thickness)
    elif style in ("double", "double_thick"):
        draw_solid_ring(margin, thickness)
        draw_solid_ring(margin + thickness + gap, thickness)
    elif style in ("dashed", "dotted"):
        dash_len = 15 if style == "dashed" else 4
        gap_len = 8 if style == "dashed" else 6
        m, th = margin, thickness

        # top & bottom dashes
        x = m
        while x < width - m:
            end = min(x + dash_len, width - m)
            mdraw.rectangle([x, m, end, m + th], fill=255)
            mdraw.rectangle([x, height - m - th, end, height - m], fill=255)
            x += dash_len + gap_len

        # left & right dashes
        y = m
        while y < height - m:
            end = min(y + dash_len, height - m)
            mdraw.rectangle([m, y, m + th, end], fill=255)
            mdraw.rectangle([width - m - th, y, width - m, end], fill=255)
            y += dash_len + gap_len

        # solid corners so the frame reads as a closed rectangle
        mdraw.rectangle([m, m, m + th, m + th], fill=255)
        mdraw.rectangle([width - m - th, m, width - m, m + th], fill=255)
        mdraw.rectangle([m, height - m - th, m + th, height - m], fill=255)
        mdraw.rectangle([width - m - th, height - m - th, width - m, height - m], fill=255)

    return mask


def draw_frame(img, gradient, margin, thickness, style="single", gap=4):
    """
    Рисует рамку по периметру с отступом от края, залитую цельным
    диагональным градиентом (не по кускам на каждую сторону/угол).
    style: single | double | double_thick | dashed | dotted
    """
    w, h = img.size
    mask = build_frame_mask(w, h, margin, thickness, style=style, gap=gap)
    grad_img = make_diagonal_gradient(w, h, gradient["start"], gradient["end"])
    img.paste(grad_img, (0, 0), mask)


# ─── TEXT HELPERS ───────────────────────────────────────────────

def truncate_text(text: str, max_len: int = 100) -> str:
    """Обрезает текст до max_len и добавляет троеточие."""
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + "…"


def decline_words(n: int) -> str:
    """
    Склонение слова "слово" для русского языка.
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


# ─── MAIN GENERATOR ─────────────────────────────────────────────

def generate_card_image(title: str, summary: str, rating: str, words: int, tags: list[str],
                        card_type: str = "text", card_format: str = "single",
                        font_regular: str = None, font_bold: str = None) -> Image.Image:
    """
    Генерирует карточку 600x600.
    """
    stripe_key, stripe_spacing, cross = get_stripe_style(words)
    border_gradient = get_border_gradient(rating, card_format)

    # Создаем изображение
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ─── ФОН КАРТОЧКИ ──────────────────────────────────────────
    if card_type == "visual":
        bg_color = BG_VISUAL
    else:
        bg_color = BG_TEXT
    draw.rectangle([0, 0, WIDTH, HEIGHT], fill=bg_color)

    # ─── FULL CARD: ПАТТЕРН ПОЛОСОК ─────────────────────────────
    STRIPE_DARKEN_VISUAL = 20  # затемнение для visual карточек
    STRIPE_DARKEN_TEXT = 5    # затемнение для text карточек
    # Затемняем цвет фона на пару тонов для страйпов
    if card_type == "visual":
        # Для светлого фона - делаем темнее
        stripe_color = tuple(max(0, c - STRIPE_DARKEN_VISUAL) for c in bg_color)
    else:
        # Для тёмного фона - делаем ещё темнее
        stripe_color = tuple(max(0, c - STRIPE_DARKEN_TEXT) for c in bg_color)
    draw_stripes(draw, WIDTH, HEIGHT, stripe_color, stripe_spacing, cross=cross)

    # ─── РАМКА ПО ФОРМАТУ ──────────────────────────────────────
    margin = 12
    if card_format == "single":
        draw_frame(img, border_gradient, margin, 10, style="single")
    elif card_format == "double":
        draw_frame(img, border_gradient, margin, 6, style="double", gap=4)
    elif card_format == "double_thick":
        draw_frame(img, border_gradient, margin, 10, style="double_thick", gap=4)
    elif card_format == "dashed":
        draw_frame(img, border_gradient, margin, 8, style="dashed")
    elif card_format == "dotted":
        draw_frame(img, border_gradient, margin, 8, style="dotted")

    # ─── MIDDLE: Title + Summary ────────────────────────────────
    top_y = HEIGHT // 4
    y = top_y
    
    default_font = ImageFont.load_default()
    title_font_size = 32

    try:
        font_title = ImageFont.truetype(font_bold, title_font_size) if font_bold else default_font
        font_summary = ImageFont.truetype(font_regular, 18) if font_regular else default_font
    except OSError:
        font_title = font_summary = default_font

    if card_type == "visual":
        text_color = "#EEEEEE"
        secondary_text_color = "#BBBBBB"
    else:
        text_color = "#3D3530"
        secondary_text_color = "#6B6055"

        # ─── MIDDLE: Title + Summary ────────────────────────────────
    top_y = HEIGHT // 4
    y = top_y

    try:
        font_title = ImageFont.truetype(font_bold, 56) if font_bold else default_font
        font_summary = ImageFont.truetype(font_regular, 18) if font_regular else default_font
    except OSError:
        font_title = font_summary = default_font

    if card_type == "visual":
        text_color = "#EEEEEE"
        secondary_text_color = "#BBBBBB"
    else:
        text_color = "#3D3530"
        secondary_text_color = "#6B6055"

    # ─── Title ─────────────────────────────────────────────────
    title_lines = textwrap.wrap(unicodedata.normalize("NFC", title), width=22)

    # Тень заголовка — тот же диагональный градиент, что и рамка,
    # вырезанный маской формы текста. Прозрачность 35%.
    mask = Image.new("L", (WIDTH, HEIGHT), 0)
    draw = ImageDraw.Draw(mask)

    for line in title_lines[:3]:
        bbox = draw.textbbox((0, 0), line, font=font_title)
        tw = bbox[2] - bbox[0]
        x = (WIDTH - tw) // 2
        draw.text((x + 2, y + 2), line, fill=255, font=font_title)
        y += title_font_size * 1.8

    shadow_grad = make_diagonal_gradient(
        WIDTH, HEIGHT, border_gradient["start"], border_gradient["end"]
    )
    arr = np.array(shadow_grad)
    arr[..., 3] = (arr[..., 3] * 0.35).astype(np.uint8)   # opacity 35%
    shadow_grad = Image.fromarray(arr, mode="RGBA")

    img.paste(shadow_grad, (0, 0), mask)

    # ─── Summary (Centered) ────────────────────────────────────
    y += 20
    summary_clean = summary.replace("\n", " ").strip()
    summary_lines = textwrap.wrap(summary_clean, width=32)
    for line in summary_lines[:6]:
        bbox = draw.textbbox((0, 0), line, font=font_summary)
        tw = bbox[2] - bbox[0]
        x = (WIDTH - tw) // 2
        draw.text((x, y), line, fill=text_color, font=font_summary)
        y += 26

    return img


# ─── DJANGO COMMAND ─────────────────────────────────────────────

class Command(BaseCommand):
    help = "Generate card images for cards without images in a season"

    def add_arguments(self, parser):
        parser.add_argument("--season-slug", type=str, required=True)
        parser.add_argument("--bot-id", type=int, required=True)
        parser.add_argument("--chat-id", type=str, required=True)
        parser.add_argument("--card-id", type=int, required=False)
        parser.add_argument("--dry-run", action="store_true", help="Save to /tmp, don't upload")

    def handle(self, *args, **options):
        season_slug = options["season_slug"]
        bot_id = options["bot_id"]
        chat_id = options["chat_id"]
        card_id = options["card_id"]
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

        cards = []
        cards_qs =  Card.objects.filter(season=season)
        if card_id:
            cards_qs =  Card.objects.filter(id__in=[card_id])
        for card in cards_qs:
            if not card.image.filter(bot=bot).exists():
                cards.append(card)

        self.stdout.write(f"Found {len(cards)} cards without images")

        success = 0
        for card in cards:
            words = 5000
            # Пробуем разные варианты разделителей строк
            for separator in ("\n", "\\n"):
                if separator in card.description:
                    for line in card.description.split(separator):
                        if line.startswith("Words:"):
                            try:
                                words = int(line.replace("Words:", "").strip().replace(",", ""))
                            except ValueError:
                                pass
                            break
                    break

            rating = "Not Rated"
            for tag in card.tags.filter(name__startswith="RatingRange: "):
                rating = tag.name.replace("RatingRange: ", "")
                break

            card_type = "text"
            card_format = "single"

            for tag in card.tags.all():
                if tag.name.startswith("Type: "):
                    type_value = tag.name.replace("Type: ", "").lower()
                    if "visual" in type_value or "art" in type_value:
                        card_type = "visual"
                        card_format = "dashed"
                    elif "challenge" in type_value:
                        card_type = "challenge"
                        card_format = "dotted"
                    elif "special" in type_value:
                        card_type = "text"
                        card_format = "dotted"
                    elif "bb" in type_value or "big bang" in type_value:
                        card_type = "bb"
                        card_format = "double_thick"
                    elif "midi" in type_value:
                        card_type = "text"
                        card_format = "double"
                    break

            # Summary — последняя строка описания (обычно там synopsis)
            summary = card.description
            for separator in ("\n", "\\n"):
                if separator in summary:
                    summary = summary.split(separator)[-1]
                    break

            img = generate_card_image(
                title=card.name,
                summary=summary,
                rating=rating,
                words=words,
                tags=[],
                card_type=card_type,
                card_format=card_format,
                font_regular=font_regular,
                font_bold=font_bold
            )

            if dry_run:
                path = f"img/card_{card.id}.png"
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