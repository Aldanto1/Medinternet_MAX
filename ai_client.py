"""Клиент к RX Code AI API (медицинская нейросеть с RAG).

Сессионный API:
  1. POST /api/chats                    -> создать сессию (SessionId)
  2. POST /api/chats/{chatId}/messages  -> отправить сообщение, получить ответ ИИ
Авторизация — заголовок Authorization: Bearer <ключ>.
"""
import json
import logging

import aiohttp

from config import NEURO_API_URL, NEURO_API_KEY, NEURO_CHANNEL

logger = logging.getLogger(__name__)

# Создание сессии быстрое; ответ ИИ может считаться долго (RAG)
_SESSION_TIMEOUT = aiohttp.ClientTimeout(total=30)
_MESSAGE_TIMEOUT = aiohttp.ClientTimeout(total=180)


class AIError(Exception):
    """Ошибка обращения к RX Code AI API."""


class SessionNotFound(AIError):
    """Сессия чата не найдена (истекла/удалена на стороне API)."""


def is_configured() -> bool:
    """True, если заданы адрес и ключ API нейросети."""
    return bool(NEURO_API_URL and NEURO_API_KEY)


def _headers() -> dict:
    return {
        "X-API-Key": NEURO_API_KEY,
        "Content-Type": "application/json",
    }


async def create_session(user_id) -> str:
    """Создаёт новую сессию чата и возвращает её id (SessionId)."""
    url = f"{NEURO_API_URL}/api/chats"
    payload = {"UserId": str(user_id), "Channel": NEURO_CHANNEL}
    async with aiohttp.ClientSession(timeout=_SESSION_TIMEOUT) as session:
        async with session.post(url, json=payload, headers=_headers()) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise AIError(f"create_session HTTP {resp.status}: {text[:200]}")
            data = await resp.json()
    chat_id = data.get("SessionId")
    if not chat_id:
        raise AIError("В ответе создания сессии нет SessionId")
    return str(chat_id)


async def send_message(chat_id: str, message: str) -> dict:
    """Отправляет сообщение в сессию и возвращает ответ ИИ.

    Результат: {"html": ..., "markdown": ..., "sources": [{"title","url"}, ...]}
    """
    url = f"{NEURO_API_URL}/api/chats/{chat_id}/messages"
    payload = {"Message": message}
    async with aiohttp.ClientSession(timeout=_MESSAGE_TIMEOUT) as session:
        async with session.post(url, json=payload, headers=_headers()) as resp:
            if resp.status == 404:
                raise SessionNotFound()
            if resp.status != 200:
                text = await resp.text()
                raise AIError(f"send_message HTTP {resp.status}: {text[:200]}")
            data = await resp.json()

    sources = []
    for s in data.get("Sources") or []:
        sources.append({"title": s.get("Title"), "url": s.get("Url")})
    return {
        "html": data.get("SummaryHTML"),
        "markdown": data.get("Summary"),
        "sources": sources,
    }


async def stream_message(chat_id: str, message: str):
    """Асинхронный генератор ответа (SSE от RX Code).

    Yield'ит кортежи (kind, value):
      ("action", "Ищу данные…")  — статус обработки
      ("text",   "кусок ответа")  — часть текста ответа (markdown)
    """
    url = f"{NEURO_API_URL}/api/chats/{chat_id}/messages/stream"
    payload = {"Message": message}
    async with aiohttp.ClientSession(timeout=_MESSAGE_TIMEOUT) as session:
        async with session.post(url, json=payload, headers=_headers()) as resp:
            if resp.status == 404:
                raise SessionNotFound()
            if resp.status != 200:
                text = await resp.text()
                raise AIError(f"stream HTTP {resp.status}: {text[:200]}")
            async for raw in resp.content:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data:
                    continue
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if obj.get("Text"):
                    yield ("text", obj["Text"])
                elif obj.get("Action"):
                    yield ("action", obj["Action"])
