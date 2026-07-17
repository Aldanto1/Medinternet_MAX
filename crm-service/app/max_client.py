"""Тонкий асинхронный клиент MAX Bot API для CRM-рассылки (aiohttp).

Аналог max_client.py основного бота, но живёт в пакете crm-service и умеет ровно
то, что нужно рассылке: отправить текст, загрузить и отправить медиа (фото/файл).

⚠️ Значения, требующие проверки на боевом токене, вынесены в config
(MAX_API_BASE, MAX_AUTH_QUERY) — см. комментарии там.
"""
import logging

import aiohttp

from app.config import MAX_BOT_TOKEN, MAX_API_BASE, MAX_AUTH_QUERY

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=60)


class MaxAPIError(Exception):
    """Ошибка MAX Bot API с кодом HTTP (для различения blocked/failed)."""

    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status
        self.message = message


class MaxClient:
    def __init__(self, token: str = MAX_BOT_TOKEN, base_url: str = MAX_API_BASE):
        self._token = (token or "").strip()
        self._base = base_url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            headers = {} if MAX_AUTH_QUERY else {"Authorization": self._token}
            self._session = aiohttp.ClientSession(timeout=_TIMEOUT, headers=headers)

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    async def _request(self, method: str, path: str, *, params=None, json=None):
        assert self._session is not None, "MaxClient.start() ещё не вызван"
        params = dict(params or {})
        if MAX_AUTH_QUERY:
            params["access_token"] = self._token
        async with self._session.request(method, f"{self._base}{path}",
                                         params=params, json=json) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise MaxAPIError(resp.status, text[:300])
            try:
                return await resp.json(content_type=None)
            except Exception:
                return {}

    async def send_message(self, *, user_id: int, text: str,
                           fmt: str | None = "html",
                           attachments: list | None = None) -> dict:
        body: dict = {"text": text, "notify": True}
        if fmt:
            body["format"] = fmt
        if attachments:
            body["attachments"] = attachments
        return await self._request("POST", "/messages", params={"user_id": user_id},
                                   json=body)

    async def upload_media_token(self, kind: str, data: bytes, filename: str) -> str | None:
        """Загружает медиа (kind: 'image' | 'file') и возвращает token вложения.

        Двухшаговый процесс MAX/TamTam: получить URL загрузки -> залить файл.
        При неудаче — None (сообщение уйдёт без вложения / со статусом failed).
        """
        assert self._session is not None
        try:
            up = await self._request("POST", "/uploads", params={"type": kind})
            url = up.get("url")
            if not url:
                return None
            form = aiohttp.FormData()
            form.add_field("data", data, filename=filename or "file")
            async with self._session.post(url, data=form) as resp:
                if resp.status >= 400:
                    return None
                payload = await resp.json(content_type=None)
            # image -> {"photos": {"<k>": {"token": ...}}}; file -> {"token": ...}
            photos = payload.get("photos") or {}
            for meta in photos.values():
                if meta.get("token"):
                    return meta["token"]
            return payload.get("token")
        except Exception as e:
            logger.warning("Загрузка медиа не удалась: %s", e)
            return None
