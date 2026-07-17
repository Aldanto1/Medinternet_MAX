"""Тонкий асинхронный клиент MAX Bot API (на aiohttp).

В MAX нет официальной Python-библиотеки (есть только JS и Go), поэтому общаемся
с REST API напрямую. Модель API унаследована от TamTam Bot API:
  • GET  /updates            — long polling, отдаёт список апдейтов и marker
  • POST /messages           — отправка сообщения (user_id / chat_id в query)
  • POST /answers            — ответ на callback-кнопку
  • GET  /me                 — информация о боте
  • POST /uploads            — загрузка медиа (фото логотипа) -> token
  • PATCH/DELETE /chats,...   — при необходимости

⚠️ ТРЕБУЕТ ПРОВЕРКИ С РЕАЛЬНЫМ ТОКЕНОМ (значения вынесены в config.py):
  • MAX_API_BASE  — базовый адрес. В разных источниках встречаются
    https://botapi.max.ru и https://platform-api2.max.ru. По умолчанию —
    botapi.max.ru; при 404/401 поменяйте в .env.
  • Авторизация — заголовок «Authorization: <token>». В старых версиях API
    токен передавался query-параметром access_token; см. MAX_AUTH_QUERY.
"""
import asyncio
import logging

import aiohttp

from config import (
    BOT_TOKEN,
    MAX_API_BASE,
    MAX_AUTH_QUERY,
)

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=60)


class MaxAPIError(Exception):
    """Ошибка обращения к MAX Bot API."""


class MaxClient:
    """Минимальный клиент MAX Bot API поверх aiohttp.ClientSession."""

    def __init__(self, token: str = BOT_TOKEN, base_url: str = MAX_API_BASE):
        # strip на случай пробелов/переносов в токене — иначе aiohttp отвергает
        # заголовок Authorization («Forbidden control character detected in headers»).
        self._token = (token or "").strip()
        self._base = base_url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def start(self) -> None:
        if self._session is None or self._session.closed:
            headers = {} if MAX_AUTH_QUERY else {"Authorization": self._token}
            self._session = aiohttp.ClientSession(timeout=_TIMEOUT, headers=headers)

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    # ---------- Низкоуровневый запрос ----------

    async def _request(self, method: str, path: str, *, params=None, json=None):
        assert self._session is not None, "MaxClient.start() ещё не вызван"
        params = dict(params or {})
        if MAX_AUTH_QUERY:
            params["access_token"] = self._token
        url = f"{self._base}{path}"
        async with self._session.request(method, url, params=params, json=json) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise MaxAPIError(f"{method} {path} -> HTTP {resp.status}: {text[:300]}")
            if not text:
                return {}
            try:
                return await resp.json(content_type=None)
            except Exception:
                return {}

    # ---------- Методы API ----------

    async def get_me(self) -> dict:
        """Информация о боте (user_id, name, username, ...)."""
        return await self._request("GET", "/me")

    async def get_updates(self, marker: int | None = None, timeout: int = 90,
                          limit: int = 100) -> dict:
        """Long polling. Возвращает {"updates": [...], "marker": <int>}."""
        params = {"timeout": timeout, "limit": limit}
        if marker is not None:
            params["marker"] = marker
        return await self._request("GET", "/updates", params=params)

    async def send_message(self, *, user_id: int | None = None,
                           chat_id: int | None = None, text: str,
                           fmt: str | None = "html",
                           attachments: list | None = None,
                           notify: bool = True) -> dict:
        """Отправка сообщения пользователю (user_id) или в чат (chat_id).

        fmt — формат текста: "html" | "markdown" | None (без разметки).
        attachments — список вложений MAX (напр. inline_keyboard, image).
        """
        params: dict = {}
        if user_id is not None:
            params["user_id"] = user_id
        if chat_id is not None:
            params["chat_id"] = chat_id
        body: dict = {"text": text, "notify": notify}
        if fmt:
            body["format"] = fmt
        if attachments:
            body["attachments"] = attachments
        return await self._request("POST", "/messages", params=params, json=body)

    async def edit_message(self, message_id: str, *, text: str,
                           fmt: str | None = "html",
                           attachments: list | None = None) -> dict:
        """Редактирование ранее отправленного сообщения по его mid."""
        body: dict = {"text": text}
        if fmt:
            body["format"] = fmt
        if attachments:
            body["attachments"] = attachments
        return await self._request("PUT", "/messages", params={"message_id": message_id}, json=body)

    async def delete_message(self, message_id: str) -> dict:
        """Удаление сообщения по его mid."""
        return await self._request("DELETE", "/messages", params={"message_id": message_id})

    async def answer_callback(self, callback_id: str, *, notification: str | None = None) -> dict:
        """Ответ на нажатие callback-кнопки (аналог callback.answer в Telegram).

        notification — всплывающее уведомление пользователю (необязательно).
        """
        body: dict = {}
        if notification:
            body["notification"] = notification
        return await self._request("POST", "/answers", params={"callback_id": callback_id}, json=body)

    async def set_my_commands(self, commands: list[dict]) -> dict:
        """Установка списка команд бота ([{"name": "start", "description": "..."}])."""
        return await self._request("PATCH", "/me", json={"commands": commands})

    async def set_my_info(self, *, description: str | None = None) -> dict:
        """Обновление профиля бота (описание и т.п.)."""
        body: dict = {}
        if description is not None:
            body["description"] = description
        return await self._request("PATCH", "/me", json=body)

    # ---------- Загрузка медиа (логотип) ----------

    async def upload_image_token(self, file_path) -> str | None:
        """Загружает изображение и возвращает token для attachment type=image.

        Двухшаговый процесс MAX/TamTam: получаем URL загрузки, затем PUT файл.
        При ошибке возвращает None (сообщение уйдёт без картинки).
        """
        assert self._session is not None
        try:
            # 1. Получаем адрес для заливки
            up = await self._request("POST", "/uploads", params={"type": "image"})
            upload_url = up.get("url")
            if not upload_url:
                return None
            # 2. Заливаем сам файл
            with open(file_path, "rb") as f:
                data = f.read()
            async with self._session.post(upload_url, data={"data": data}) as resp:
                if resp.status >= 400:
                    return None
                payload = await resp.json(content_type=None)
            # Ответ содержит photos/token для использования в attachment
            photos = payload.get("photos") or {}
            for meta in photos.values():
                if meta.get("token"):
                    return meta["token"]
            return payload.get("token")
        except Exception as e:
            logger.warning("Не удалось загрузить изображение %s: %s", file_path, e)
            return None
