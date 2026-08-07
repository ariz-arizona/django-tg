import os
import asyncio
import logging
import aiohttp
from curl_cffi import requests as curl_requests

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from server.logger import logger

FLARESOLVERR_URL = os.getenv("FLARESOLVERR_URL", "http://flaresolverr:8191/v1")
SESSION_ID = "tarot_bot_session"  # постоянная сессия

# ── Управление сессией ──────────────────────────────────────────

async def create_flaresolverr_session(max_timeout: int = 120000) -> bool:
    """Создает постоянную сессию во FlareSolverr."""
    try:
        payload = {
            "cmd": "sessions.create",
            "session": SESSION_ID,
            "maxTimeout": max_timeout,
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                FLARESOLVERR_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                result = await resp.json()
                
                if result.get("status") == "ok":
                    logger.info(f"✅ FlareSolverr session '{SESSION_ID}' created successfully")
                    return True
                else:
                    logger.error(f"❌ Failed to create session: {result.get('message', 'Unknown error')}")
                    return False
                    
    except Exception as e:
        logger.error(f"❌ Error creating FlareSolverr session: {e}")
        return False


async def destroy_flaresolverr_session():
    """Уничтожает сессию при завершении работы."""
    try:
        payload = {
            "cmd": "sessions.destroy",
            "session": SESSION_ID,
        }    
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                FLARESOLVERR_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                await resp.json()
                logger.info(f"🗑️ FlareSolverr session '{SESSION_ID}' destroyed")
                
    except Exception as e:
        logger.warning(f"⚠️ Failed to destroy session: {e}")


# ── Основная функция запроса ──────────────────────────────────

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=5, max=30),
    retry=retry_if_exception_type((aiohttp.ClientError, TimeoutError)),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
async def fetch_via_flaresolverr(url: str, max_timeout: int = 120000) -> str:
    """
    Выполняет запрос через FlareSolverr с одной постоянной сессией.
    """
    payload = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": max_timeout,
        "session": SESSION_ID,  # одна и та же сессия
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(
            FLARESOLVERR_URL,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=max_timeout/1000 + 10),
        ) as resp:
            result = await resp.json()
    
    # Проверяем ответ
    if result.get("status") != "ok":
        msg = result.get("message", "")
        
        # Если сессия мертва — пересоздаём и ретраим
        if "session" in msg.lower() or "timeout" in msg.lower():
            logger.warning(f"⚠️ Session dead, recreating: {msg}")
            await create_flaresolverr_session(max_timeout)
            raise aiohttp.ClientError(f"Session recreated, retrying...")
        
        raise RuntimeError(f"FlareSolverr error: {msg}")
    
    # Логируем использование сессии (можно добавить счетчик)
    logger.debug(f"✅ Request successful via session '{SESSION_ID}'")
    return result["solution"]["response"]


# ── Инициализация при старте бота ─────────────────────────────

async def init_flaresolverr():
    """Вызвать при запуске бота."""
    success = await create_flaresolverr_session()
    if not success:
        logger.error("❌ Failed to initialize FlareSolverr session!")
    return success


async def shutdown_flaresolverr():
    """Вызвать при остановке бота."""
    await destroy_flaresolverr_session()


# ── Альтернатива: функция с автоматической инициализацией ──

_is_initialized = False

async def ensure_flaresolverr_session(max_timeout: int = 120000):
    """Проверяет, что сессия существует, если нет — создает."""
    global _is_initialized
    
    if _is_initialized:
        return True
    
    # Пробуем создать сессию
    success = await create_flaresolverr_session(max_timeout)
    if success:
        _is_initialized = True
        return True
    
    # Если не получилось, пробуем еще раз через 5 секунд
    await asyncio.sleep(5)
    success = await create_flaresolverr_session(max_timeout)
    if success:
        _is_initialized = True
        return True
    
    return False


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=5, max=30),
    retry=retry_if_exception_type((aiohttp.ClientError, TimeoutError)),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
async def fetch_via_flaresolverr_auto(url: str, max_timeout: int = 120000) -> str:
    """Автоматически создает сессию при первом вызове."""
    # Гарантируем, что сессия существует
    if not await ensure_flaresolverr_session(max_timeout):
        raise RuntimeError("Failed to create FlareSolverr session")
    
    # Используем ту же логику, что и выше
    payload = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": max_timeout,
        "ttl": 3600, 
        "session": SESSION_ID,
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(
            FLARESOLVERR_URL,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=max_timeout/1000 + 10),
        ) as resp:
            result = await resp.json()
    
    if result.get("status") != "ok":
        msg = result.get("message", "")
        if "session" in msg.lower() or "timeout" in msg.lower():
            logger.warning(f"⚠️ Session dead, resetting...")
            _is_initialized = False  # сброс флага
            raise aiohttp.ClientError(f"Session dead, will recreate")
        raise RuntimeError(f"FlareSolverr error: {msg}")
    
    return result["solution"]["response"]

async def tarot_fetch(url: str) -> str:
    """Асинхронный запрос к tarot.com через curl_cffi"""
    def _fetch():
        return curl_requests.get(
            url,
            impersonate="chrome",
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            }
        )
    
    response = await asyncio.to_thread(_fetch)
    return response.text