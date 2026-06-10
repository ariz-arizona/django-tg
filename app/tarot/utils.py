import ssl
import aiohttp
from aiohttp import ClientTimeout, ClientConnectorError
import asyncio
from server.logger import logger

async def download_image_aiohttp(file_url):
    """Асинхронная загрузка изображения с правильной обработкой ошибок"""
    
    # 1. Настраиваем таймаут (30 сек на соединение, 60 на чтение)
    timeout = ClientTimeout(total=60, connect=30, sock_read=60)
    
    # 2. Создаем SSL контекст (игнорируем самоподписанные сертификаты)
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    
    # 3. Настраиваем заголовки
    headers = {
        'User-Agent': 'Mozilla/5.0 (compatible; TelegramBot/1.0)',
        'Accept': 'image/jpeg,image/webp,image/*'
    }
    
    try:
        # Создаем connector с настройками
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        
        async with aiohttp.ClientSession(
            timeout=timeout,
            headers=headers,
            connector=connector
        ) as session:
            async with session.get(file_url) as response:
                if response.status == 200:
                    # Читаем данные
                    img_data = await response.read()
                    
                    # Проверяем, что это реально изображение
                    content_type = response.headers.get('Content-Type', '')
                    if not content_type.startswith('image/'):
                        logger.warning(f"Не image: {content_type}")
                    
                    return img_data
                else:
                    logger.error(f"Ошибка {response.status}: {file_url}")
                    return None
                    
    except asyncio.TimeoutError:
        logger.error(f"Таймаут при загрузке {file_url}")
        return None
    except ClientConnectorError as e:
        logger.error(f"Ошибка соединения {file_url}: {e}")
        return None
    except Exception as e:
        logger.error(f"Неизвестная ошибка {file_url}: {e}")
        return None