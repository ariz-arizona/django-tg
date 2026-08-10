# utils/image_utils.py

import os
import io
import aiohttp
from io import BytesIO
from typing import List, Dict, Optional
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops
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



# ───────────────────────────────────────────────
# 7. ЗАГРУЗКА КАРТ ДЛЯ SPREAD
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
    max_card_width: int = 600,
    max_file_size: int = 10 * 1024 * 1024,  # 10 MB — максимум для Telegram photo
    jpeg_quality: int = 90
) -> Optional[BytesIO]:
    """
    Основная функция для spread: скачивает карты и создаёт изображение расклада.
    Всегда сохраняет в JPEG с автоматическим уменьшением размера при необходимости.

    Args:
        cards_data: список словарей с картами
        spacing: отступы между картами
        row_spacing: отступы между рядами
        max_card_width: максимальная ширина карты
        max_file_size: максимальный размер файла в байтах (по умолч. 10 MB)
        jpeg_quality: начальное качество JPEG (90%)

    Returns:
        BytesIO с JPEG-изображением или None при ошибке
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

        # === Конвертация в RGB для JPEG ===
        if canvas.mode in ('RGBA', 'P'):
            rgb_canvas = Image.new('RGB', canvas.size, (255, 255, 255))
            rgb_canvas.paste(canvas, mask=canvas.split()[-1] if canvas.mode == 'RGBA' else None)
        else:
            rgb_canvas = canvas.convert('RGB')

        # === Сохранение в JPEG с автоподбором размера ===

        # Пробуем разные качества: 90 → 85 → 80 → 75 → 70
        qualities = [jpeg_quality, 85, 80, 75, 70]

        for quality in qualities:
            result = BytesIO()
            rgb_canvas.save(result, format='JPEG', quality=quality, optimize=True)
            result.seek(0)
            file_size = result.getbuffer().nbytes

            logger.info(f"JPEG качество {quality}%: {file_size / 1024 / 1024:.2f} MB")

            if file_size <= max_file_size:
                logger.info(f"✅ Файл готов: JPEG {quality}%, {file_size} байт")
                result.seek(0)
                return result

        # Если и JPEG слишком большой — уменьшаем размеры изображения
        logger.warning("JPEG всё ещё слишком большой, уменьшаем размеры...")

        scale = 0.9
        while scale > 0.3:  # Минимум 30% от оригинала
            new_size = (int(rgb_canvas.width * scale), int(rgb_canvas.height * scale))
            resized = rgb_canvas.resize(new_size, Image.Resampling.LANCZOS)

            result = BytesIO()
            resized.save(result, format='JPEG', quality=85, optimize=True)
            result.seek(0)
            final_size = result.getbuffer().nbytes

            logger.info(f"Масштаб {scale*100:.0f}% ({new_size[0]}x{new_size[1]}): {final_size / 1024 / 1024:.2f} MB")

            if final_size <= max_file_size:
                logger.info(f"✅ Файл готов: JPEG 85%, масштаб {scale*100:.0f}%, {final_size} байт")
                result.seek(0)
                return result

            scale -= 0.1

        logger.error("Не удалось уложиться в лимит даже при сильном уменьшении")
        return None

    except Exception as e:
        logger.error(f"Ошибка создания изображения расклада: {e}", exc_info=True)
        return None


def create_card_row(
    card_images: List[Image.Image], 
    spacing: int = 10, 
    max_cards_per_row: int = 3, 
    fixed_width: bool = False
) -> Image.Image:
    """
    Создает горизонтальный ряд карт.
    Отступы между картами = spacing.
    Вокруг ряда padding НЕ добавляется — отступы контролируются вызывающим кодом.

    Логика:
    - 1-3 карты (fixed_width=False): ширина динамическая по картам
    - >3 карты (fixed_width=True): ширина на max_cards_per_row карт
    """
    if not card_images:
        return None

    num_cards = len(card_images)

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
        canvas_width = total_cards_width
        canvas_height = max_height
        canvas = Image.new('RGBA', (canvas_width, canvas_height), (0, 0, 0, 0))

        x_offset = 0
        for img in normalized_cards:
            y_offset = (max_height - img.height) // 2
            canvas.paste(img, (x_offset, y_offset), img if img.mode == 'RGBA' else None)
            x_offset += img.width + spacing

        return canvas
    else:
        sample_cards = normalized_cards[:max_cards_per_row]
        row_width = sum(img.width for img in sample_cards) + spacing * (len(sample_cards) - 1)

        canvas_width = row_width
        canvas_height = max_height
        canvas = Image.new('RGBA', (canvas_width, canvas_height), (0, 0, 0, 0))

        start_x = (row_width - total_cards_width) // 2

        x_offset = start_x
        for img in normalized_cards:
            y_offset = (max_height - img.height) // 2
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

async def _load_font(size: int = 20, monospace: bool = False):
    """Ленивая загрузка шрифта."""
    if monospace:
        # Моноширинные шрифты для ASCII-заголовков
        mono_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeMono.ttf",
            "/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf",
        ]
        for path in mono_paths:
            try:
                return ImageFont.truetype(path, size)
            except:
                continue

    # Обычные шрифты
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]

    for path in font_paths:
        try:
            return ImageFont.truetype(path, size)
        except:
            continue

    return ImageFont.load_default()


def _get_text_height(font: ImageFont.FreeTypeFont) -> int:
    """Возвращает высоту текста для заданного шрифта."""
    bbox = font.getbbox("Ag")
    return bbox[3] - bbox[1]


def _place_card_on_canvas(
    card_img: Image.Image,
    rotation: float = 0,
    border_color: tuple = (240, 230, 210),
    border_width: int = 1,
    shadow: bool = True,
    edge_blur: float = 0.0,  # убираем, т.к. проблема не в альфа
) -> Image.Image:
    """
    Подготавливает карту: рамка → upscale → поворот → downscale → тень.
    """
    if card_img.mode != 'RGBA':
        card_img = card_img.convert('RGBA')
    
    # 1. РАМКА (до поворота)
    if border_width > 0:
        draw = ImageDraw.Draw(card_img)
        draw.rectangle(
            [0, 0, card_img.width - 1, card_img.height - 1],
            outline=border_color + (255,),
            width=border_width
        )

    # 2. ПОВОРОТ с upscale для качества
    if rotation != 0:
        # Увеличиваем в 2× перед поворотом
        scale = 2
        big = card_img.resize(
            (card_img.width * scale, card_img.height * scale),
            Image.Resampling.LANCZOS
        )
        
        # Поворачиваем увеличенную версию
        big_rotated = big.rotate(rotation, expand=True, resample=Image.Resampling.BICUBIC)
        
        # Уменьшаем обратно — края станут гораздо плавнее
        card_img = big_rotated.resize(
            (big_rotated.width // scale, big_rotated.height // scale),
            Image.Resampling.BICUBIC
        )

    # 3. ТЕНЬ
    if shadow:
        shadow_offset = 4
        shadow_blur = 6
        shadow_pad = shadow_blur + shadow_offset
        
        alpha = card_img.split()[3]
        shadow_mask = alpha.filter(ImageFilter.GaussianBlur(radius=shadow_blur))
        
        shadow_rgba = Image.merge('RGBA', [
            Image.new('L', shadow_mask.size, 0),
            Image.new('L', shadow_mask.size, 0),
            Image.new('L', shadow_mask.size, 0),
            ImageChops.multiply(shadow_mask, Image.new('L', shadow_mask.size, 60))
        ])
        
        result = Image.new('RGBA', 
            (card_img.width + shadow_pad * 2, 
             card_img.height + shadow_pad * 2),
            (0, 0, 0, 0))
        
        result.paste(shadow_rgba, (shadow_pad + shadow_offset, shadow_pad + shadow_offset), shadow_mask)
        result.paste(card_img, (shadow_pad, shadow_pad), card_img)
        
        card_img = result

    return card_img

async def create_full_spread_image(
    cards: List[Dict],
    username: str = "",
    deck_name: str = "",
    spread_title: str = "",
    bot_username: str = "",
    canvas_width: int = CANVAS_WIDTH,
    canvas_height: int = CANVAS_HEIGHT,
    card_width: int = CARD_WIDTH,
    card_height: int = CARD_HEIGHT,
    card_margin: int = CARD_MARGIN,
    canvas_bg: tuple = CANVAS_BG,
    jpeg_quality: int = 90,  # ← новый параметр
) -> Optional[io.BytesIO]:
    """
    Создаёт полное изображение расклада на холсте.
    Возвращает JPEG для меньшего размера и быстрой отправки.

    Логика размещения:
    - 1 карта: по центру холста
    - 2-3 карты: по центру в ряд
    - >3 карт: сетка с наложением + мини-сигнификатор поверх
    """
    from datetime import datetime

    num_cards = len(cards)
    if num_cards == 0:
        return None

    # Рендерим в зависимости от количества карт
    if num_cards == 1:
        result = await _render_single_card(cards, username, deck_name, spread_title, bot_username,
                                           canvas_width, canvas_height, canvas_bg)
    elif num_cards <= 3:
        result = await _render_center_row(cards, username, deck_name, spread_title, bot_username,
                                          canvas_width, canvas_height, card_width, card_height,
                                          card_margin, canvas_bg)
    else:
        result = await _render_many_cards(cards, username, deck_name, spread_title, bot_username,
                                          canvas_width, canvas_height, card_width, card_height,
                                          card_margin, canvas_bg)

    if result is None:
        return None

    # ─── КОНВЕРТАЦИЯ В JPEG ───
    try:
        result.seek(0)
        img = Image.open(result)

        # RGBA → RGB с фоном canvas_bg
        if img.mode == 'RGBA':
            background = Image.new('RGB', img.size, canvas_bg)
            background.paste(img, mask=img.split()[3])
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')

        # Сохраняем в JPEG
        jpeg_buffer = io.BytesIO()
        img.save(jpeg_buffer, format='JPEG', quality=jpeg_quality, optimize=True)
        jpeg_buffer.seek(0)

        logger.info(f"Spread image: PNG → JPEG, качество {jpeg_quality}, "
                   f"размер {len(jpeg_buffer.getvalue()) / 1024:.1f} KB")

        return jpeg_buffer

    except Exception as e:
        logger.warning(f"Не удалось конвертировать в JPEG, возвращаем PNG: {e}")
        result.seek(0)
        return result

async def _render_single_card(
    cards: List[Dict],
    username: str,
    deck_name: str,
    spread_title: str,
    bot_username: str,
    canvas_width: int,
    canvas_height: int,
    canvas_bg: tuple,
) -> Optional[io.BytesIO]:
    """1 карта по центру холста."""
    from datetime import datetime

    PAD = 20
    HEADER_FONT_SIZE = 14
    FOOTER_FONT_SIZE = 11

    canvas = Image.new('RGBA', (canvas_width, canvas_height), canvas_bg)
    draw = ImageDraw.Draw(canvas)

    # Подпись сверху
    header_font = await _load_font(HEADER_FONT_SIZE)
    header_h = _get_text_height(header_font)
    header_y = PAD + header_h // 2
    if spread_title:
        draw.text((canvas_width // 2, header_y), spread_title, fill=(220, 220, 220), font=header_font, anchor="mm")

    # Подпись снизу
    footer_font = await _load_font(FOOTER_FONT_SIZE, monospace=True)
    dt_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    if username and bot_username:
        footer_text = f"@{bot_username} | @{username} | {dt_str}"
    elif bot_username:
        footer_text = f"@{bot_username} | {dt_str}"
    else:
        footer_text = dt_str
    footer_h = _get_text_height(footer_font)
    footer_y = canvas_height - PAD - footer_h // 2
    draw.text((canvas_width // 2, footer_y), footer_text, fill=(140, 140, 160), font=footer_font, anchor="mm")

    # Карта по центру
    card_data = cards[0]
    card_img = _get_card_image(card_data)
    if card_img is None:
        return None

    # Доступная область
    available_height = canvas_height - PAD * 2 - header_h - footer_h - 10
    available_width = canvas_width - PAD * 2

    # Масштабируем карту
    scale_w = available_width / card_img.width
    scale_h = available_height / card_img.height
    scale = min(scale_w, scale_h, 1.0)

    if scale < 1.0:
        new_w = int(card_img.width * scale)
        new_h = int(card_img.height * scale)
        card_img = card_img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    # Подготавливаем карту (рамка, тень)
    prepared = _place_card_on_canvas(card_img, rotation=0, shadow=True)

    # Центрируем
    x_pos = PAD + (available_width - prepared.width) // 2
    y_pos = PAD + header_h + 5 + (available_height - prepared.height) // 2

    # Накладываем на холст
    canvas.paste(prepared, (x_pos, y_pos), prepared)

    output = io.BytesIO()
    canvas.save(output, format='PNG', quality=95)
    output.seek(0)
    return output


async def _render_center_row(
    cards: List[Dict],
    username: str,
    deck_name: str,
    spread_title: str,
    bot_username: str,
    canvas_width: int,
    canvas_height: int,
    card_width: int,
    card_height: int,
    card_margin: int,
    canvas_bg: tuple,
) -> Optional[io.BytesIO]:
    """2-3 карты по центру в один ряд."""
    from datetime import datetime

    PAD = 20
    HEADER_FONT_SIZE = 14
    FOOTER_FONT_SIZE = 11

    canvas = Image.new('RGBA', (canvas_width, canvas_height), canvas_bg)
    draw = ImageDraw.Draw(canvas)

    # Подпись сверху
    header_font = await _load_font(HEADER_FONT_SIZE)
    header_h = _get_text_height(header_font)
    header_y = PAD + header_h // 2
    if spread_title:
        draw.text((canvas_width // 2, header_y), spread_title, fill=(220, 220, 220), font=header_font, anchor="mm")

    # Подпись снизу
    footer_font = await _load_font(FOOTER_FONT_SIZE, monospace=True)
    dt_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    if username and bot_username:
        footer_text = f"@{bot_username} | @{username} | {dt_str}"
    elif bot_username:
        footer_text = f"@{bot_username} | {dt_str}"
    else:
        footer_text = dt_str
    footer_h = _get_text_height(footer_font)
    footer_y = canvas_height - PAD - footer_h // 2
    draw.text((canvas_width // 2, footer_y), footer_text, fill=(140, 140, 160), font=footer_font, anchor="mm")

    num_cards = len(cards)

    # Доступная область
    available_height = canvas_height - PAD * 2 - header_h - footer_h - 10
    available_width = canvas_width - PAD * 2

    # Рассчитываем размер одной карты
    total_card_width = num_cards * card_width + (num_cards - 1) * card_margin
    scale_w = available_width / total_card_width
    scale_h = available_height / card_height
    scale = min(scale_w, scale_h, 1.0)

    render_w = int(card_width * scale)
    render_h = int(card_height * scale)
    render_margin = int(card_margin * scale)

    total_width = num_cards * render_w + (num_cards - 1) * render_margin
    start_x = PAD + (available_width - total_width) // 2
    y_pos = PAD + header_h + 5 + (available_height - render_h) // 2

    for i, card_data in enumerate(cards):
        x_pos = start_x + i * (render_w + render_margin)
        card_img = _get_card_image(card_data)
        if card_img is None:
            continue

        # Масштабируем
        if card_img.width != render_w or card_img.height != render_h:
            card_img = card_img.resize((render_w, render_h), Image.Resampling.LANCZOS)

        # Подготавливаем (рамка, тень)
        prepared = _place_card_on_canvas(card_img, rotation=0, shadow=True)

        # Накладываем на холст
        canvas.paste(prepared, (x_pos, y_pos), prepared)

    output = io.BytesIO()
    canvas.save(output, format='PNG', quality=95)
    output.seek(0)
    return output


async def _render_many_cards(
    cards: List[Dict],
    username: str,
    deck_name: str,
    spread_title: str,
    bot_username: str,
    canvas_width: int,
    canvas_height: int,
    card_width: int,
    card_height: int,
    card_margin: int,
    canvas_bg: tuple,
) -> Optional[io.BytesIO]:
    """>3 карт: сетка с наложением + мини-сигнификатор."""
    from datetime import datetime
    import random

    num_cards = len(cards)
    PAD = 20
    HEADER_FONT_SIZE = 14
    FOOTER_FONT_SIZE = 11

    # Фоновый холст (RGB)
    canvas = Image.new('RGB', (canvas_width, canvas_height), canvas_bg)
    draw = ImageDraw.Draw(canvas)

    # Подписи на фоне
    header_font = await _load_font(HEADER_FONT_SIZE)
    header_h = _get_text_height(header_font)
    header_y = PAD + header_h // 2
    if spread_title:
        draw.text((canvas_width // 2, header_y), spread_title, fill=(220, 220, 220), font=header_font, anchor="mm")

    footer_font = await _load_font(FOOTER_FONT_SIZE, monospace=True)
    dt_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    if username and bot_username:
        footer_text = f"@{bot_username} | @{username} | {dt_str}"
    elif bot_username:
        footer_text = f"@{bot_username} | {dt_str}"
    else:
        footer_text = dt_str
    footer_h = _get_text_height(footer_font)
    footer_y = canvas_height - PAD - footer_h // 2
    draw.text((canvas_width // 2, footer_y), footer_text, fill=(140, 140, 160), font=footer_font, anchor="mm")

    # Доступная область
    cards_top = PAD + header_h + 5
    cards_bottom = footer_y - 5
    available_height = cards_bottom - cards_top
    available_width = canvas_width - PAD * 2

    # Сетка
    grid_cards = cards[1:]
    grid_count = len(grid_cards)

    overlap_h = 0.08

    # Подбираем колонки
    best_cols = 3
    best_card_area = 0

    for cols in range(1, min(grid_count + 1, 17)):
        rows = math.ceil(grid_count / cols)
        denom = cols - (cols - 1) * overlap_h
        gw = int(available_width / denom)
        gh = int(gw * (card_height / card_width))
        total_h = rows * gh
        if total_h > available_height or gw <= 0 or gh <= 0:
            continue
        card_area = gw * gh
        if card_area > best_card_area or (card_area == best_card_area and cols < best_cols):
            best_card_area = card_area
            best_cols = cols

    cols = best_cols
    rows = math.ceil(grid_count / cols)

    denom = cols - (cols - 1) * overlap_h
    grid_card_width = int(available_width / denom)
    grid_card_height = int(grid_card_width * (card_height / card_width))

    total_grid_width = int(cols * grid_card_width - (cols - 1) * grid_card_width * overlap_h)
    total_grid_height = rows * grid_card_height

    if total_grid_height > available_height:
        scale_factor = available_height / total_grid_height
        grid_card_width = int(grid_card_width * scale_factor)
        grid_card_height = int(grid_card_height * scale_factor)
        total_grid_width = int(cols * grid_card_width - (cols - 1) * grid_card_width * overlap_h)
        total_grid_height = rows * grid_card_height

    grid_start_x = PAD + (available_width - total_grid_width) // 2
    grid_start_y = cards_top + (available_height - total_grid_height) // 2

    # Собираем карты
    card_layers = []

    for i, card_data in enumerate(grid_cards):
        row = i // cols
        col = i % cols

        cards_in_row = cols if row < rows - 1 else grid_count - (rows - 1) * cols
        if cards_in_row < cols:
            row_width = int(cards_in_row * grid_card_width - (cards_in_row - 1) * grid_card_width * overlap_h)
            row_offset_x = (total_grid_width - row_width) // 2
        else:
            row_offset_x = 0

        x_pos = grid_start_x + row_offset_x + int(col * grid_card_width * (1 - overlap_h))
        y_pos = grid_start_y + row * grid_card_height

        card_img = _get_card_image(card_data)
        if card_img is None:
            continue

        # Ресайз
        grid_img = card_img.resize((grid_card_width, grid_card_height), Image.Resampling.LANCZOS)

        # Поворот + рамка + тень — ВСЁ в _place_card_on_canvas
        rotation = random.choice([-3, -2, -1, 0, 1, 2, 3])
        prepared = _place_card_on_canvas(
            grid_img,
            rotation=rotation,
            border_color=(240, 230, 210),
            border_width=1,
            shadow=True
        )

        card_layers.append((x_pos, y_pos, prepared))

    # Мини-сигнификатор
    mini_scale = 0.30
    mini_width = int(card_width * mini_scale)
    mini_height = int(card_height * mini_scale)

    mini_x = canvas_width - PAD - mini_width
    mini_y = PAD

    first_card = cards[0]
    first_img = _get_card_image(first_card)
    if first_img:
        mini_img = first_img.copy()
        mini_img = mini_img.resize((mini_width, mini_height), Image.Resampling.LANCZOS)
        prepared_mini = _place_card_on_canvas(
            mini_img,
            rotation=0,
            border_color=(240, 230, 210),
            border_width=1,
            shadow=True
        )
        card_layers.append((mini_x, mini_y, prepared_mini))

    # Flatten
    composite = Image.new('RGBA', (canvas_width, canvas_height), (0, 0, 0, 0))

    for x, y, card_img in card_layers:
        composite.paste(card_img, (x, y), card_img)

    canvas_rgba = canvas.convert('RGBA')
    final = Image.alpha_composite(canvas_rgba, composite)
    final_rgb = final.convert('RGB')

    output = io.BytesIO()
    final_rgb.save(output, format='PNG', quality=95)
    output.seek(0)
    return output


def _get_card_image(card_data: Dict) -> Optional[Image.Image]:
    """Извлекает и подготавливает изображение карты из card_data."""
    card_item = card_data.get("card_instance") or card_data.get("card_item")
    if not card_item:
        return None
    card_img = card_data.get("_image")
    if card_img is None:
        return None
    return card_img.copy()