import asyncio
import logging

import aiohttp
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from server.logger import logger

FLARESOLVERR_URL = "http://flaresolverr:8191/v1"


class FlareSolverrError(Exception):
    pass


class FlareSolverrSession:
    """
    Персистентная сессия FlareSolverr для одного домена.
    Создаём один раз, переиспользуем, убиваем при ошибках.
    """

    def __init__(self):
        self.session_id: str | None = None
        self._lock = asyncio.Lock()

    async def _create(self) -> str:
        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                FLARESOLVERR_URL,
                json={"cmd": "sessions.create"},
                timeout=aiohttp.ClientTimeout(total=30),
            )
            result = await resp.json()
            if result.get("status") != "ok":
                raise FlareSolverrError("Failed to create session")
            self.session_id = result["session"]
            logger.info(f"FlareSolverr session created: {self.session_id}")
            return self.session_id

    async def get(self) -> str:
        async with self._lock:
            if self.session_id is None:
                return await self._create()
            return self.session_id

    async def destroy(self):
        async with self._lock:
            if self.session_id:
                try:
                    async with aiohttp.ClientSession() as session:
                        await session.post(
                            FLARESOLVERR_URL,
                            json={"cmd": "sessions.destroy", "session": self.session_id},
                            timeout=aiohttp.ClientTimeout(total=10),
                        )
                except Exception as e:
                    logger.warning(f"Failed to destroy session: {e}")
                finally:
                    self.session_id = None

    async def reset(self):
        await self.destroy()
        return await self.get()


# Глобальная сессия для tarot.com
flaresolverr_session = FlareSolverrSession()


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=5, max=30),
    retry=retry_if_exception_type((FlareSolverrError, aiohttp.ClientError, TimeoutError)),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
async def fetch_via_flaresolverr(url: str, max_timeout: int = 120000) -> str:
    session_id = await flaresolverr_session.get()

    payload = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": max_timeout,
        "session": session_id,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            FLARESOLVERR_URL,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=130),
        ) as resp:
            result = await resp.json()

    # Сессия протухла — сбрасываем и ретраим
    if result.get("status") != "ok":
        msg = result.get("message", "")
        if "session" in msg.lower() or "timeout" in msg.lower():
            logger.warning(f"Session dead, resetting: {msg}")
            await flaresolverr_session.reset()
            raise FlareSolverrError(f"Session reset required: {msg}")
        raise FlareSolverrError(msg)

    return result["solution"]["response"]