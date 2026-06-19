# utils/image_utils.py

import aiohttp
from io import BytesIO
from typing import List, Dict, Optional
from PIL import Image, ImageDraw
import logging
import math

from server.logger import logger

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


def process_card_image(img_data: bytes, flipped: bool = False, max_width: int = 600) -> Optional[Image.Image]:
    """
    Обрабатывает изображение карты: открывает, изменяет размер, поворачивает если нужно.
    
    Args:
        img_data: байты изображения
        flipped: перевёрнута ли карта
        max_width: максимальная ширина после изменения размера
        
    Returns:
        Обработанное изображение или None при ошибке
    """
    try:
        img = Image.open(BytesIO(img_data))
        
        # Уменьшаем ширину, если нужно
        if img.width > max_width:
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


def create_card_row(card_images: List[Image.Image], spacing: int = 10, 
                    max_cards_per_row: int = 3, fixed_width: bool = False) -> Image.Image:
    """
    Создает горизонтальный ряд карт с центрированием и полями.
    Поля вокруг карт = spacing * 2
    
    Логика:
    - 1-3 карты (fixed_width=False): ширина динамическая по картам, элементы центрированы
    - 3-10 карт (fixed_width=True): ширина на три карты, элементы центрированы
    
    Args:
        card_images: список изображений карт
        spacing: расстояние между картами
        max_cards_per_row: максимальное количество карт в ряду (3 для таро)
        fixed_width: фиксированная ширина под 3 карты или динамическая
        
    Returns:
        Изображение с рядом карт
    """
    if not card_images:
        return None
    
    num_cards = len(card_images)
    padding = spacing * 2  # Поля вокруг карт в 2 раза больше spacing
    
    # Находим максимальную высоту среди карт для нормализации
    max_height = max(img.height for img in card_images)
    
    # Приводим все карты к одинаковой высоте (максимальной)
    normalized_cards = []
    for img in card_images:
        if img.height != max_height:
            ratio = max_height / img.height
            new_width = int(img.width * ratio)
            img = img.resize((new_width, max_height), Image.Resampling.LANCZOS)
        normalized_cards.append(img)
    
    # Вычисляем общую ширину для всех карт с отступами
    total_cards_width = sum(img.width for img in normalized_cards) + spacing * (len(normalized_cards) - 1)
    
    if not fixed_width or num_cards <= max_cards_per_row:
        # Динамическая ширина: холст под все карты + поля
        canvas_width = total_cards_width + padding * 2
        canvas_height = max_height + padding * 2
        canvas = Image.new('RGBA', (canvas_width, canvas_height), (0, 0, 0, 0))
        
        # Размещаем карты с учетом полей
        x_offset = padding
        for img in normalized_cards:
            # Центрируем каждую карту по вертикали с учетом полей
            y_offset = padding + (max_height - img.height) // 2
            canvas.paste(img, (x_offset, y_offset), img if img.mode == 'RGBA' else None)
            x_offset += img.width + spacing
        
        return canvas
    else:
        # Фиксированная ширина на 3 карты: холст размером под 3 карты + поля
        # Вычисляем ширину для 3 карт (берем первые 3 карты для расчета)
        sample_cards = normalized_cards[:max_cards_per_row]
        three_cards_width = sum(img.width for img in sample_cards) + spacing * (len(sample_cards) - 1)
        
        canvas_width = three_cards_width + padding * 2
        canvas_height = max_height + padding * 2
        canvas = Image.new('RGBA', (canvas_width, canvas_height), (0, 0, 0, 0))
        
        # Вычисляем начальную позицию для центрирования всех карт с учетом полей
        start_x = padding + (three_cards_width - total_cards_width) // 2
        
        # Размещаем карты по центру
        x_offset = start_x
        for img in normalized_cards:
            # Центрируем каждую карту по вертикали с учетом полей
            y_offset = padding + (max_height - img.height) // 2
            canvas.paste(img, (x_offset, y_offset), img if img.mode == 'RGBA' else None)
            x_offset += img.width + spacing
        
        return canvas


def create_multiple_rows(card_images: List[Image.Image], spacing: int = 10, 
                         max_cards_per_row: int = 3, row_spacing: int = 20) -> Image.Image:
    """
    Создает несколько рядов карт, если их больше 3.
    
    Args:
        card_images: список изображений карт
        spacing: расстояние между картами в ряду
        max_cards_per_row: максимальное количество карт в ряду
        row_spacing: расстояние между рядами
        
    Returns:
        Полное изображение со всеми рядами
    """
    if len(card_images) <= max_cards_per_row:
        return create_card_row(card_images, spacing, max_cards_per_row, fixed_width=False)
    
    rows = []
    for i in range(0, len(card_images), max_cards_per_row):
        row_cards = card_images[i:i + max_cards_per_row]
        # Для рядов с картами больше 3 используем фиксированную ширину
        is_last_row = i + max_cards_per_row >= len(card_images)
        row_image = create_card_row(
            row_cards, 
            spacing, 
            max_cards_per_row, 
            fixed_width=not is_last_row or len(row_cards) > 1  # Фиксированная ширина для полных рядов
        )
        rows.append(row_image)
    
    # Находим максимальную ширину среди всех рядов
    max_width = max(row.width for row in rows)
    total_height = sum(row.height for row in rows) + row_spacing * (len(rows) - 1)
    
    # Создаем общий холст
    final_canvas = Image.new('RGBA', (max_width, total_height), (0, 0, 0, 0))
    
    # Размещаем ряды с центрированием по ширине
    y_offset = 0
    for row in rows:
        # Центрируем каждый ряд по горизонтали
        x_offset = (max_width - row.width) // 2
        final_canvas.paste(row, (x_offset, y_offset), row if row.mode == 'RGBA' else None)
        y_offset += row.height + row_spacing
    
    return final_canvas


async def load_card_images(
    cards_data: List[Dict], 
    max_width: int = 600
) -> List[Dict]:
    """
    Загружает и обрабатывает изображения карт.
    
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
        
        # Скачиваем изображение
        img_data = await download_image_aiohttp(file_path)
        if not img_data:
            logger.warning(f"Не удалось скачать изображение {file_path}")
            continue
        
        # Обрабатываем изображение
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


def create_spread_layout(card_images: List[Dict], spacing: int = 10, 
                         row_spacing: int = 20) -> Optional[Image.Image]:
    """
    Создает изображение расклада с правильной логикой ширины и полями:
    - Поля вокруг карт = spacing * 2
    - 1-3 карты: динамическая ширина
    - 4-10 карт: ширина на 3 карты с центрированием
    
    Args:
        card_images: список словарей с изображениями карт
        spacing: расстояние между картами
        row_spacing: расстояние между рядами
        
    Returns:
        Готовое изображение расклада
    """
    if not card_images:
        return None
    
    images = [item['image'] for item in card_images]
    num_cards = len(images)
    
    if num_cards <= 3:
        # 1-3 карты: динамическая ширина, один ряд
        return create_card_row(images, spacing, fixed_width=False)
    else:
        # 4-10 карт: ширина на 3 карты, несколько рядов
        return create_multiple_rows(images, spacing, max_cards_per_row=3, row_spacing=row_spacing)


async def create_spread_image(
    cards_data: List[Dict],
    spacing: int = 10,
    row_spacing: int = 20,
    max_card_width: int = 600
) -> Optional[BytesIO]:
    """
    Основная функция: скачивает карты и создаёт изображение расклада.
    
    Args:
        cards_data: список словарей с картами
        options: параметры расклада (может использоваться для заголовка и т.д.)
        spacing: отступы между картами
        row_spacing: отступы между рядами
        max_card_width: максимальная ширина карты
        
    Returns:
        BytesIO с изображением или None при ошибке
    """
    try:
        # Загружаем изображения
        card_images = await load_card_images(cards_data, max_width=max_card_width)
        
        if not card_images:
            logger.error("Не удалось загрузить ни одной карты")
            return None
        
        # Создаём расклад с правильной логикой
        canvas = create_spread_layout(card_images, spacing=spacing, row_spacing=row_spacing)
        
        if not canvas:
            return None
        
        logger.info(f"Создано изображение расклада с {len(card_images)} картами")
        
        # Сохраняем в BytesIO
        result = BytesIO()
        canvas.save(result, format='PNG')
        result.seek(0)
        
        return result
        
    except Exception as e:
        logger.error(f"Ошибка создания изображения расклада: {e}")
        return None