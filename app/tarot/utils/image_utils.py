# utils/image_utils.py

import os
import io
import aiohttp
from io import BytesIO
from typing import List, Dict, Optional
from PIL import Image, ImageDraw, ImageFont
import logging
import math

from server.logger import logger


# ───────────────────────────────────────────────
# Константы для RWS-рендерера (из canvas_handler)
# ───────────────────────────────────────────────
RWS_DECK_ID = 63
RWS_CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "tarot", "rws_cache")
CANVAS_WIDTH = 900
CANVAS_HEIGHT = 1200
CARD_WIDTH = 260
CARD_HEIGHT = 450
CARD_MARGIN = 20
CANVAS_BG = (26, 26, 46)  # Тёмно-синий #1a1a2e
CANVAS_PADDING = 40


# ───────────────────────────────────────────────
# 1. СКАЧИВАНИЕ
# ───────────────────────────────────────────────

async def download_image_aiohttp(url: str) -> Optional[bytes]:
    """Скачивает изображение по URL."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.read()
                else:
                    logger.error(f"Ошибка скачивания {url}: {response.status}")
                    return None
    except Exception as e:
        logger.error(f"Исключение при скачивании {url}: {e}")
        return None


# ───────────────────────────────────────────────
# 2. ОБРАБОТКА ИЗОБРАЖЕНИЯ (унифицированная)
# ───────────────────────────────────────────────

def process_card_image(
    img_data: bytes, 
    flipped: bool = False, 
    max_width: int = 600,
    max_height: Optional[int] = None
) -> Optional[Image.Image]:
    """
    Обрабатывает изображение карты: открывает, изменяет размер, поворачивает если нужно.

    Args:
        img_data: байты изображения
        flipped: перевёрнута ли карта
        max_width: максимальная ширина после изменения размера
        max_height: максимальная высота (если задана, приоритетнее max_width)

    Returns:
        Обработанное изображение или None при ошибке
    """
    try:
        img = Image.open(BytesIO(img_data))

        # Уменьшаем по заданным ограничениям
        if max_height and img.height > max_height:
            ratio = max_height / img.height
            new_width = int(img.width * ratio)
            img = img.resize((new_width, max_height), Image.Resampling.LANCZOS)
        elif img.width > max_width:
            ratio = max_width / img.width
            new_height = int(img.height * ratio)
            img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)

        # Поворачиваем если перевёрнута
        if flipped:
            img = img.rotate(180, expand=True)

        return img
    except Exception as e:
        logger.error(f"Ошибка обработки изображения: {e}")
        return None


# ───────────────────────────────────────────────
# 3. РАБОТА С КЭШЕМ RWS (из canvas_handler)
# ───────────────────────────────────────────────

def _ensure_rws_cache_dir():
    """Создаёт директорию для кэша RWS-карт, если её нет."""
    os.makedirs(RWS_CACHE_DIR, exist_ok=True)


def get_rws_cache_path(card_id: int, card_name: str) -> str:
    """Возвращает путь к кэшированному файлу карты."""
    card_key = f"{card_id}_{card_name}"
    return os.path.join(RWS_CACHE_DIR, f"{card_key}.png")


def load_rws_image_from_disk(cache_path: str) -> Optional[Image.Image]:
    """Загружает RWS-карту с диска."""
    if not os.path.exists(cache_path):
        return None
    try:
        img = Image.open(cache_path).convert("RGBA")
        logger.debug(f"RWS карта загружена с диска: {cache_path}")
        return img
    except Exception as e:
        logger.warning(f"Не удалось загрузить карту с диска {cache_path}: {e}")
        return None


def save_rws_image_to_disk(img: Image.Image, cache_path: str) -> bool:
    """Сохраняет RWS-карту на диск."""
    try:
        img.save(cache_path, "PNG")
        logger.info(f"RWS карта сохранена на диск: {cache_path}")
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения карты на диск {cache_path}: {e}")
        return False


async def get_rws_card_image(
    card_id: int,
    card_name: str,
    file_link: str,
    card_width: int = CARD_WIDTH,
    card_height: int = CARD_HEIGHT
) -> Optional[Image.Image]:
    """
    Получает изображение карты RWS.
    Сначала проверяет диск, потом качает по ссылке, сохраняет на диск.

    Args:
        card_id: ID карты
        card_name: имя карты для ключа кэша
        file_link: публичная ссылка на файл
        card_width: целевая ширина
        card_height: целевая высота

    Returns:
        PIL.Image или None
    """
    _ensure_rws_cache_dir()
    cache_path = get_rws_cache_path(card_id, card_name)

    # 1. Проверяем диск
    img = load_rws_image_from_disk(cache_path)
    if img is not None:
        return img

    # 2. Скачиваем по ссылке
    img_data = await download_image_aiohttp(file_link)
    if not img_data:
        logger.error(f"Не удалось скачать карту {card_id} по ссылке")
        return None

    # 3. Обрабатываем и ресайзим
    try:
        img = Image.open(BytesIO(img_data)).convert("RGBA")
        img.thumbnail((card_width, card_height), Image.Resampling.LANCZOS)

        # 4. Сохраняем на диск
        save_rws_image_to_disk(img, cache_path)

        return img
    except Exception as e:
        logger.error(f"Ошибка обработки карты {card_id}: {e}", exc_info=True)
        return None


# ───────────────────────────────────────────────
# 4. ЛЕЙАУТ КАРТ (из utils — уже унифицировано)
# ───────────────────────────────────────────────

def create_card_row(
    card_images: List[Image.Image], 
    spacing: int = 10, 
    max_cards_per_row: int = 3, 
    fixed_width: bool = False
) -> Image.Image:
    """
    Создает горизонтальный ряд карт с центрированием и полями.
    Поля вокруг карт = spacing * 2

    Логика:
    - 1-3 карты (fixed_width=False): ширина динамическая по картам, элементы центрированы
    - 3-10 карт (fixed_width=True): ширина на три карты, элементы центрированы
    """
    if not card_images:
        return None

    num_cards = len(card_images)
    padding = spacing * 2

    # Находим максимальную высоту среди карт для нормализации
    max_height = max(img.height for img in card_images)

    # Приводим все карты к одинаковой высоте
    normalized_cards = []
    for img in card_images:
        if img.height != max_height:
            ratio = max_height / img.height
            new_width = int(img.width * ratio)
            img = img.resize((new_width, max_height), Image.Resampling.LANCZOS)
        normalized_cards.append(img)

    total_cards_width = sum(img.width for img in normalized_cards) + spacing * (len(normalized_cards) - 1)

    if not fixed_width or num_cards <= max_cards_per_row:
        canvas_width = total_cards_width + padding * 2
        canvas_height = max_height + padding * 2
        canvas = Image.new('RGBA', (canvas_width, canvas_height), (0, 0, 0, 0))

        x_offset = padding
        for img in normalized_cards:
            y_offset = padding + (max_height - img.height) // 2
            canvas.paste(img, (x_offset, y_offset), img if img.mode == 'RGBA' else None)
            x_offset += img.width + spacing

        return canvas
    else:
        sample_cards = normalized_cards[:max_cards_per_row]
        three_cards_width = sum(img.width for img in sample_cards) + spacing * (len(sample_cards) - 1)

        canvas_width = three_cards_width + padding * 2
        canvas_height = max_height + padding * 2
        canvas = Image.new('RGBA', (canvas_width, canvas_height), (0, 0, 0, 0))

        start_x = padding + (three_cards_width - total_cards_width) // 2

        x_offset = start_x
        for img in normalized_cards:
            y_offset = padding + (max_height - img.height) // 2
            canvas.paste(img, (x_offset, y_offset), img if img.mode == 'RGBA' else None)
            x_offset += img.width + spacing

        return canvas


def create_multiple_rows(
    card_images: List[Image.Image], 
    spacing: int = 10, 
    max_cards_per_row: int = 3, 
    row_spacing: int = 20
) -> Image.Image:
    """Создает несколько рядов карт, если их больше 3."""
    if len(card_images) <= max_cards_per_row:
        return create_card_row(card_images, spacing, max_cards_per_row, fixed_width=False)

    rows = []
    for i in range(0, len(card_images), max_cards_per_row):
        row_cards = card_images[i:i + max_cards_per_row]
        is_last_row = i + max_cards_per_row >= len(card_images)
        row_image = create_card_row(
            row_cards, 
            spacing, 
            max_cards_per_row, 
            fixed_width=not is_last_row or len(row_cards) > 1
        )
        rows.append(row_image)

    max_width = max(row.width for row in rows)
    total_height = sum(row.height for row in rows) + row_spacing * (len(rows) - 1)

    final_canvas = Image.new('RGBA', (max_width, total_height), (0, 0, 0, 0))

    y_offset = 0
    for row in rows:
        x_offset = (max_width - row.width) // 2
        final_canvas.paste(row, (x_offset, y_offset), row if row.mode == 'RGBA' else None)
        y_offset += row.height + row_spacing

    return final_canvas


def create_spread_layout(
    card_images: List[Dict], 
    spacing: int = 10, 
    row_spacing: int = 20
) -> Optional[Image.Image]:
    """
    Создает изображение расклада:
    - 1-3 карты: динамическая ширина
    - 4-10 карт: ширина на 3 карты с центрированием
    """
    if not card_images:
        return None

    images = [item['image'] for item in card_images]
    num_cards = len(images)

    if num_cards <= 3:
        return create_card_row(images, spacing, fixed_width=False)
    else:
        return create_multiple_rows(images, spacing, max_cards_per_row=3, row_spacing=row_spacing)


# ───────────────────────────────────────────────
# 5. ПОЛНЫЙ РЕНДЕР РАСКЛАДА (из canvas_handler)
# ───────────────────────────────────────────────

async def _load_font(size: int = 20):
    """Ленивая загрузка шрифта."""
    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
    except:
        return ImageFont.load_default()


def _place_card_on_canvas(
    canvas: Image.Image,
    card_img: Image.Image,
    x: int,
    y: int,
    rotation: float = 0
):
    """Размещает карту на холсте с тенью, поворотом и перевёрнутостью."""
    # Применяем поворот
    if rotation != 0:
        card_img = card_img.rotate(rotation, expand=True, resample=Image.Resampling.BICUBIC)

    # Рисуем тень
    shadow = Image.new('RGBA', card_img.size, (0, 0, 0, 80))
    shadow_offset = 4
    canvas.paste(shadow, (x + shadow_offset, y + shadow_offset), shadow)

    # Размещаем карту
    canvas.paste(card_img, (x, y), card_img)


async def create_full_spread_image(
    cards: List[Dict],
    username: str = "",
    canvas_width: int = CANVAS_WIDTH,
    canvas_height: int = CANVAS_HEIGHT,
    card_width: int = CARD_WIDTH,
    card_height: int = CARD_HEIGHT,
    card_margin: int = CARD_MARGIN,
    canvas_bg: tuple = CANVAS_BG,
) -> Optional[io.BytesIO]:
    """
    Создаёт полное изображение расклада на холсте с заголовком и подписью.

    Args:
        cards: список словарей с данными карт (card_item с keys: id, name_en/name, flipped)
        username: имя пользователя для подписи
        deck_name: название колоды-источника

    Returns:
        BytesIO с PNG-изображением или None
    """
    from datetime import datetime
    import random

    # Создаём холст
    canvas = Image.new('RGB', (canvas_width, canvas_height), canvas_bg)
    draw = ImageDraw.Draw(canvas)
    font = await _load_font(20)

    # Заголовок
    title = f"Расклад для @{username}" if username else "Расклад"
    draw.text((canvas_width // 2, 30), title, fill=(255, 255, 255), font=font, anchor="mm")

    num_cards = len(cards)
    if num_cards == 0:
        return None

    # Первая строка: до 3 карт по центру
    top_row_count = min(3, num_cards)
    top_row_cards = cards[:top_row_count]

    total_width = top_row_count * card_width + (top_row_count - 1) * card_margin
    start_x = (canvas_width - total_width) // 2
    y_pos = 100

    for i, card_data in enumerate(top_row_cards):
        x_pos = start_x + i * (card_width + card_margin)

        card_item = card_data.get("card_instance") or card_data.get("card_item")
        if not card_item:
            continue

        # Получаем изображение (должно быть предзагружено)
        card_img = card_data.get("_image")
        if card_img is None:
            continue

        # Копируем для модификации
        card_img = card_img.copy()

        # Переворачиваем если нужно
        if card_data.get("flipped"):
            card_img = card_img.rotate(180, expand=True)

        _place_card_on_canvas(canvas, card_img, x_pos, y_pos)

    # Остальные карты: россыпь снизу
    remaining = cards[top_row_count:]
    if remaining:
        y_pos = 100 + card_height + card_margin * 2
        cards_per_row = 3

        for i, card_data in enumerate(remaining):
            row = i // cards_per_row
            col = i % cards_per_row

            row_count = min(cards_per_row, len(remaining) - row * cards_per_row)
            row_width = row_count * card_width + (row_count - 1) * card_margin
            row_start_x = (canvas_width - row_width) // 2

            x_pos = row_start_x + col * (card_width + card_margin)
            current_y = y_pos + row * (card_height + card_margin)

            rotation = random.uniform(-3, 3)

            card_item = card_data.get("card_instance") or card_data.get("card_item")
            if not card_item:
                continue

            card_img = card_data.get("_image")
            if card_img is None:
                continue

            card_img = card_img.copy()

            if card_data.get("flipped"):
                card_img = card_img.rotate(180, expand=True)

            _place_card_on_canvas(canvas, card_img, x_pos, current_y, rotation)

    # Подпись внизу
    footer_y = canvas_height - 40
    date_str = datetime.now().strftime("%d.%m.%Y")
    footer_text = f"@YourBotName • {date_str}"
    draw.text((canvas_width // 2, footer_y), footer_text, fill=(150, 150, 150), font=font, anchor="mm")

    # Сохраняем в BytesIO
    output = io.BytesIO()
    canvas.save(output, format='PNG', quality=95)
    output.seek(0)
    return output


# ───────────────────────────────────────────────
# 6. ЗАГРУЗКА КАРТ ДЛЯ SPREAD (из utils)
# ───────────────────────────────────────────────

async def load_card_images(
    cards_data: List[Dict], 
    max_width: int = 600
) -> List[Dict]:
    """
    Загружает и обрабатывает изображения карт для spread.

    Args:
        cards_data: список словарей с ключами 'file_path', 'flipped', 'name'
        max_width: максимальная ширина карты

    Returns:
        Список словарей с обработанными изображениями
    """
    card_images = []

    for idx, card_data in enumerate(cards_data):
        file_path = card_data.get('file_path')
        if not file_path:
            logger.warning(f"Нет file_path для карты {idx}")
            continue

        img_data = await download_image_aiohttp(file_path)
        if not img_data:
            logger.warning(f"Не удалось скачать изображение {file_path}")
            continue

        img = process_card_image(
            img_data, 
            flipped=card_data.get('flipped', False),
            max_width=max_width
        )

        if img:
            card_images.append({
                'image': img,
                'name': card_data.get('name', f'Card {idx+1}'),
                'original_height': img.height,
                'original_width': img.width
            })

    return card_images


async def create_spread_image(
    cards_data: List[Dict],
    spacing: int = 10,
    row_spacing: int = 20,
    max_card_width: int = 600
) -> Optional[BytesIO]:
    """
    Основная функция для spread: скачивает карты и создаёт изображение расклада.

    Args:
        cards_data: список словарей с картами
        spacing: отступы между картами
        row_spacing: отступы между рядами
        max_card_width: максимальная ширина карты

    Returns:
        BytesIO с изображением или None при ошибке
    """
    try:
        card_images = await load_card_images(cards_data, max_width=max_card_width)

        if not card_images:
            logger.error("Не удалось загрузить ни одной карты")
            return None

        canvas = create_spread_layout(card_images, spacing=spacing, row_spacing=row_spacing)

        if not canvas:
            return None

        logger.info(f"Создано изображение расклада с {len(card_images)} картами")

        result = BytesIO()
        canvas.save(result, format='PNG')
        result.seek(0)

        return result

    except Exception as e:
        logger.error(f"Ошибка создания изображения расклада: {e}", exc_info=True)
        return None