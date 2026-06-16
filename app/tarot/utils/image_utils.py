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