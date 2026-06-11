# utils/image_utils.py

import aiohttp
from io import BytesIO
from typing import List, Dict, Optional
from PIL import Image, ImageDraw
import logging

logger = logging.getLogger(__name__)


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
    Создаёт холст и размещает на нём изображения в ряд.
    
    Args:
        images_data: список словарей с ключами 'image', 'original_width', 'original_height'
        spacing: отступ между изображениями и по краям
        
    Returns:
        Собранное изображение или None при ошибке
    """
    if not images_data:
        logger.error("Нет изображений для размещения")
        return None
    
    # Рассчитываем размеры холста
    total_width = sum(img['original_width'] for img in images_data) + spacing * (len(images_data) - 1)
    max_height = max(img['original_height'] for img in images_data)
    
    canvas_width = total_width + (spacing * 2)
    canvas_height = (spacing * 2) + max_height
    
    logger.info(f"Рассчитан размер холста: {canvas_width}x{canvas_height}")
    
    # Создаём холст
    canvas = Image.new('RGB', (canvas_width, canvas_height), color='white')
    
    # Размещаем изображения
    current_x = spacing
    y_position = spacing
    
    for img_data in images_data:
        canvas.paste(img_data['image'], (current_x, y_position))
        current_x += img_data['original_width'] + spacing
    
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