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


def create_card_row(card_images: List[Image.Image], spacing: int = 10, max_cards_per_row: int = 3) -> Image.Image:
    """
    Создает горизонтальный ряд карт с центрированием.
    
    Логика:
    - 1-3 карты: холст по размеру максимальной карты, остальные центрируются
    - 4+ карт: холст по размеру 3 карт, карты располагаются рядами
    
    Args:
        card_images: список изображений карт
        spacing: расстояние между картами
        max_cards_per_row: максимальное количество карт в ряду (3 для таро)
        
    Returns:
        Изображение с рядом карт
    """
    if not card_images:
        return None
    
    num_cards = len(card_images)
    
    # Если карт 3 или меньше - центрируем в холсте размером с максимальную карту
    if num_cards <= max_cards_per_row:
        # Находим максимальные размеры среди карт
        max_width = max(img.width for img in card_images)
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
        
        # Создаем холст размером с максимальную карту (по ширине - под все карты)
        canvas_width = max(max_width, total_cards_width)
        canvas = Image.new('RGBA', (canvas_width, max_height), (0, 0, 0, 0))
        
        # Вычисляем начальную позицию для центрирования всех карт
        start_x = (canvas_width - total_cards_width) // 2
        
        # Размещаем карты по центру
        x_offset = start_x
        for img in normalized_cards:
            # Центрируем каждую карту по вертикали
            y_offset = (max_height - img.height) // 2
            canvas.paste(img, (x_offset, y_offset), img if img.mode == 'RGBA' else None)
            x_offset += img.width + spacing
        
        return canvas
    
    # Если карт больше 3 - холст по размеру 3 карт, остальные в следующих рядах
    else:
        # Здесь логика для 4+ карт (если нужно несколько рядов)
        # Пока возвращаем первый ряд из 3 карт
        first_row_cards = card_images[:max_cards_per_row]
        
        # Находим максимальную высоту для первого ряда
        max_height = max(img.height for img in first_row_cards)
        
        # Приводим все карты первого ряда к одной высоте
        normalized_cards = []
        for img in first_row_cards:
            if img.height != max_height:
                ratio = max_height / img.height
                new_width = int(img.width * ratio)
                img = img.resize((new_width, max_height), Image.Resampling.LANCZOS)
            normalized_cards.append(img)
        
        # Вычисляем общую ширину для 3 карт
        total_width = sum(img.width for img in normalized_cards) + spacing * (len(normalized_cards) - 1)
        
        # Создаем холст точно под 3 карты
        canvas = Image.new('RGBA', (total_width, max_height), (0, 0, 0, 0))
        
        # Размещаем карты
        x_offset = 0
        for img in normalized_cards:
            canvas.paste(img, (x_offset, 0), img if img.mode == 'RGBA' else None)
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
        return create_card_row(card_images, spacing, max_cards_per_row)
    
    rows = []
    for i in range(0, len(card_images), max_cards_per_row):
        row_cards = card_images[i:i + max_cards_per_row]
        row_image = create_card_row(row_cards, spacing, max_cards_per_row)
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

def create_canvas_from_images(
    images_data: List[Dict],
    spacing: int = 30
) -> Optional[Image.Image]:
    """
    Размещает изображения сеткой (до 3 в ряд).
    """
    if not images_data:
        return None

    # Константы сетки
    items_per_row = 3
    num_images = len(images_data)
    num_rows = math.ceil(num_images / items_per_row)

    # Рассчитываем размеры: 
    # Ширина = (ширина 3 картинок + отступы между ними) + внешние поля
    # Но так как картинки могут быть разными, берем макс. ширину в ряду
    # Для простоты предположим, что все карты примерно одного размера
    img_w = images_data[0]['original_width']
    img_h = images_data[0]['original_height']
    
    # Ширина холста = (ширина * 3) + (отступы: 2 внутри + 2 по краям)
    canvas_width = (img_w * items_per_row) + (spacing * (items_per_row + 1))
    # Высота холста = (высота * ряды) + (отступы: (ряды - 1) + 2 по краям)
    canvas_height = (img_h * num_rows) + (spacing * (num_rows + 1))
    
    canvas = Image.new('RGB', (canvas_width, canvas_height), color='white')
    
    for index, img_data in enumerate(images_data):
        # Вычисляем ряд и колонку
        row = index // items_per_row
        col = index % items_per_row
        
        # Вычисляем координаты
        x = spacing + col * (img_w + spacing)
        y = spacing + row * (img_h + spacing)
        
        canvas.paste(img_data['image'], (x, y))
    
    return canvas


async def create_spread_image(
    cards_data: List[Dict],
    options: Dict[str, any],
    spacing: int = 30,
    max_card_width: int = 600
) -> Optional[BytesIO]:
    """
    Основная функция: скачивает карты и создаёт изображение расклада.
    
    Args:
        cards_data: список словарей с картами
        options: параметры расклада (может использоваться для заголовка и т.д.)
        spacing: отступы между элементами
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
        
        # Создаём холст с изображениями
        canvas = create_canvas_from_images(card_images, spacing=spacing)
        
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