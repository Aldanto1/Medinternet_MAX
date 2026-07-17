"""Отправка сообщений пользователям MAX токеном основного бота.

Возвращает статус доставки: "sent" / "blocked" / "failed".
Пользователь, остановивший/заблокировавший бота, недоступен для отправки —
MAX отвечает ошибкой (трактуем 403 как "blocked").
"""
import asyncio
import logging

from app.max_client import MaxClient, MaxAPIError

logger = logging.getLogger(__name__)

_client: MaxClient | None = None


async def init() -> None:
    global _client
    _client = MaxClient()
    await _client.start()


async def close() -> None:
    if _client is not None:
        await _client.close()


def _status_from_error(e: MaxAPIError) -> str:
    # 403 — пользователь остановил/заблокировал бота (отправка запрещена).
    return "blocked" if e.status == 403 else "failed"


async def send(user_id: int, text: str) -> str:
    """Отправляет текст. Возвращает 'sent' / 'blocked' / 'failed'."""
    assert _client is not None, "bot_client.init() ещё не вызван"
    try:
        await _client.send_message(user_id=user_id, text=text, fmt="html")
        return "sent"
    except MaxAPIError as e:
        if e.status == 429:  # флуд-контроль: подождать и повторить один раз
            await asyncio.sleep(2)
            try:
                await _client.send_message(user_id=user_id, text=text, fmt="html")
                return "sent"
            except MaxAPIError as e2:
                return _status_from_error(e2)
        if e.status != 403:
            logger.warning("Ошибка отправки %s: %s", user_id, e)
        return _status_from_error(e)
    except Exception as e:
        logger.warning("Ошибка отправки %s: %s", user_id, e)
        return "failed"


async def send_media(
    user_id: int,
    kind: str,                 # 'photo' | 'document'
    caption: str | None,
    *,
    token: str | None = None,  # уже загруженный token вложения (переиспользуем)
    data: bytes | None = None,
    filename: str | None = None,
) -> tuple[str, str | None]:
    """Отправляет фото/документ. Если token не задан — грузит data и возвращает
    полученный token для переиспользования в остальных отправках рассылки.

    Возвращает (status, token|None), status ∈ 'sent'/'blocked'/'failed'.
    """
    assert _client is not None, "bot_client.init() ещё не вызван"
    upload_kind = "image" if kind == "photo" else "file"
    attach_type = "image" if kind == "photo" else "file"

    new_token = None
    if token is None:
        if data is None:
            return "failed", None
        token = await _client.upload_media_token(upload_kind, data, filename or "file")
        if not token:
            return "failed", None
        new_token = token

    attachments = [{"type": attach_type, "payload": {"token": token}}]
    try:
        await _client.send_message(user_id=user_id, text=caption or "",
                                   fmt="html", attachments=attachments)
        return "sent", new_token
    except MaxAPIError as e:
        if e.status == 429:
            await asyncio.sleep(2)
            try:
                await _client.send_message(user_id=user_id, text=caption or "",
                                           fmt="html", attachments=attachments)
                return "sent", new_token
            except MaxAPIError as e2:
                return _status_from_error(e2), new_token
        if e.status != 403:
            logger.warning("Ошибка отправки media %s: %s", user_id, e)
        return _status_from_error(e), new_token
    except Exception as e:
        logger.warning("Ошибка отправки media %s: %s", user_id, e)
        return "failed", new_token
