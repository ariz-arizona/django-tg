import os
import asyncio
import logging
import time
from itertools import cycle
from threading import Lock

from curl_cffi import requests as curl_requests

logger = logging.getLogger(__name__)

# ── Proxy pool with health tracking ────────────────────────────

WEBSHARE_LOGIN = os.getenv("WEBSHARE_LOGIN", "")
WEBSHARE_PASSWORD = os.getenv("WEBSHARE_PASSWORD", "")

_raw_proxies = os.getenv("WEBSHARE_PROXIES", "")

# Все прокси из env
_ALL_PROXIES = []
if _raw_proxies and WEBSHARE_LOGIN and WEBSHARE_PASSWORD:
    _ALL_PROXIES = [
        f"http://{WEBSHARE_LOGIN}:{WEBSHARE_PASSWORD}@{host_port.strip()}/"
        for host_port in _raw_proxies.split(",")
        if host_port.strip()
    ]
    logger.info(f"Loaded {len(_ALL_PROXIES)} proxies from env")

# "Живые" прокси (может уменьшаться)
_proxy_pool = _ALL_PROXIES.copy()
_proxy_lock = Lock()

# Счётчик неудач по прокси (для возврата в пул через время)
_proxy_failures = {}  # proxy_url -> timestamp
_PROXY_BAN_TIME = 3600  # 1 час — потом попробуем снова


def _get_working_proxies() -> list[str]:
    """Возвращает список рабочих прокси, возвращая в пул протухшие баны."""
    now = time.time()
    with _proxy_lock:
        # Возвращаем прокси, у которых бан истёк
        recovered = [
            p for p, t in list(_proxy_failures.items())
            if now - t > _PROXY_BAN_TIME and p not in _proxy_pool
        ]
        for p in recovered:
            _proxy_pool.append(p)
            del _proxy_failures[p]
            logger.info(f"Proxy recovered from ban: {p.rsplit('@', 1)[-1]}")
        
        return _proxy_pool.copy()


def _ban_proxy(proxy_url: str):
    """Убираем прокси из пула на время."""
    with _proxy_lock:
        if proxy_url in _proxy_pool:
            _proxy_pool.remove(proxy_url)
            _proxy_failures[proxy_url] = time.time()
            logger.warning(
                f"Proxy banned ({len(_proxy_pool)} left): {proxy_url.rsplit('@', 1)[-1]}"
            )


def get_next_proxy() -> dict | None:
    """Берём следующий рабочий прокси. Если пусто — None (прямой запрос)."""
    working = _get_working_proxies()
    if not working:
        logger.error("No working proxies left! Trying direct request...")
        return None
    
    # Просто берём первый доступный (можно рандом или round-robin)
    proxy_url = working[0]
    logger.debug(f"Using proxy: {proxy_url.rsplit('@', 1)[-1]}")
    return {"http": proxy_url, "https": proxy_url}


# ── WB fetch with auto-retry on bad proxy ─────────────────────

async def wb_fetch_with_session(api_url: str, timeout: int = 30, max_retries: int = 3) -> curl_requests.Response:
    """
    Двухэтапный запрос с автоподбором прокси.
    Если 403 — баним прокси и пробуем следующий.
    """
    last_error = None
    
    for attempt in range(max_retries):
        proxy_dict = get_next_proxy()
        proxy_url = proxy_dict["http"] if proxy_dict else None
        
        def _fetch():
            proxy_kwargs = {}
            if proxy_dict:
                proxy_kwargs["proxies"] = proxy_dict

            # Этап 1: фронт для cookies
            front_resp = curl_requests.get(
                "https://www.wildberries.ru/",
                impersonate="chrome120",
                timeout=timeout,
                **proxy_kwargs,
            )
            logger.debug(f"Front: {front_resp.status_code}, cookies: {list(front_resp.cookies.keys())}")

            # Этап 2: API с cookies
            return curl_requests.get(
                api_url,
                impersonate="chrome120",
                headers={
                    "Accept": "application/json",
                    "Referer": "https://www.wildberries.ru/",
                },
                cookies=dict(front_resp.cookies),
                timeout=timeout,
                **proxy_kwargs,
            )

        try:
            response = await asyncio.to_thread(_fetch)
            
            if response.status_code == 200:
                return response
            
            # 403 — скорее всего прокси в бане
            if response.status_code == 403 and proxy_url:
                logger.warning(f"403 on attempt {attempt + 1}, banning proxy")
                _ban_proxy(proxy_url)
                last_error = f"403 with proxy {proxy_url}"
                continue  # пробуем следующий прокси
            
            # Другая ошибка — не прокси виноват
            return response
            
        except Exception as e:
            logger.error(f"Request failed on attempt {attempt + 1}: {e}")
            if proxy_url:
                _ban_proxy(proxy_url)
            last_error = str(e)
    
    # Все попытки исчерпаны
    raise RuntimeError(f"All proxies failed. Last error: {last_error}")


async def wb_fetch_image(url: str, timeout: int = 30) -> curl_requests.Response:
    """Для картинок — без фронта, с авто-ротацией прокси при 403."""
    proxy_dict = get_next_proxy()
    
    def _fetch():
        kwargs = {
            "impersonate": "chrome120",
            "timeout": timeout,
        }
        if proxy_dict:
            kwargs["proxies"] = proxy_dict
        return curl_requests.get(url, **kwargs)

    response = await asyncio.to_thread(_fetch)
    
    # Если 403 — баним и пробуем ещё раз с другим
    if response.status_code == 403 and proxy_dict:
        _ban_proxy(proxy_dict["http"])
        return await wb_fetch_image(url, timeout)  # рекурсия с новым прокси
    
    return response


async def wb_fetch_head(url: str, timeout: int = 30) -> curl_requests.Response:
    """HEAD с авто-ротацией."""
    proxy_dict = get_next_proxy()
    
    def _fetch():
        kwargs = {
            "impersonate": "chrome120",
            "timeout": timeout,
        }
        if proxy_dict:
            kwargs["proxies"] = proxy_dict
        return curl_requests.head(url, **kwargs)

    response = await asyncio.to_thread(_fetch)
    
    if response.status_code == 403 and proxy_dict:
        _ban_proxy(proxy_dict["http"])
        return await wb_fetch_head(url, timeout)
    
    return response


# ── Stats ─────────────────────────────────────────────────────

def get_proxy_stats() -> dict:
    """Для мониторинга — сколько живых, сколько в бане."""
    with _proxy_lock:
        return {
            "total": len(_ALL_PROXIES),
            "working": len(_proxy_pool),
            "banned": len(_proxy_failures),
            "banned_list": [p.rsplit("@", 1)[-1] for p in _proxy_failures.keys()],
        }