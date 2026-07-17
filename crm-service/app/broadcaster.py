"""Внутрипроцессная очередь рассылки (без Redis).

API и воркер живут в одном процессе, поэтому нагрузку (текст/файл) держим в памяти,
а задания раздаём через asyncio.Queue. Один воркер + пауза = гарантированный темп
≤ SEND_RATE_PER_SEC (лимит MAX Bot API ~30/сек). Статусы пишутся в crm.broadcast_log,
поэтому панель опрашивает прогресс так же, как раньше.

Ограничение по сравнению с Redis-версией: рассылка «в полёте» не переживает
перезапуск сервиса (для текущего масштаба приемлемо).
"""
import asyncio
import logging

from app import bot_client, db
from app.config import SEND_RATE_PER_SEC

logger = logging.getLogger(__name__)

_DELAY = 1.0 / SEND_RATE_PER_SEC

_queue: "asyncio.Queue[tuple[str, int]]" = asyncio.Queue()
_payloads: dict[str, dict] = {}   # broadcast_id -> {caption, kind, filename, data, token}
_worker_task: asyncio.Task | None = None


def store_payload(broadcast_id: str, caption: str, kind: str,
                  filename: str | None, data: bytes | None) -> None:
    _payloads[broadcast_id] = {
        "caption": caption, "kind": kind, "filename": filename,
        "data": data, "token": None,
    }


async def enqueue(broadcast_id: str, user_ids: list[int]) -> None:
    for uid in user_ids:
        await _queue.put((broadcast_id, uid))


async def _handle(broadcast_id: str, user_id: int) -> None:
    payload = _payloads.get(broadcast_id)
    if payload is None:
        await db.update_log_status(broadcast_id, user_id, "failed")
        return

    caption = payload.get("caption") or ""
    kind = payload.get("kind") or "text"

    if kind == "text":
        status = await bot_client.send(user_id, caption)
    else:
        # Медиа: первый раз грузим файл и кэшируем token, дальше шлём по token.
        token = payload.get("token")
        data = None if token else payload.get("data")
        status, new_token = await bot_client.send_media(
            user_id, kind, caption, token=token, data=data,
            filename=payload.get("filename"),
        )
        if new_token:
            payload["token"] = new_token

    if status == "blocked":
        await db.mark_blocked(user_id)
    await db.update_log_status(broadcast_id, user_id, status)


async def _worker() -> None:
    logger.info("CRM broadcaster запущен (темп ≤ %s/сек)", SEND_RATE_PER_SEC)
    while True:
        broadcast_id, user_id = await _queue.get()
        try:
            await _handle(broadcast_id, user_id)
        except Exception as e:
            logger.warning("Ошибка рассылки %s -> %s: %s", broadcast_id, user_id, e)
        finally:
            _queue.task_done()
        await asyncio.sleep(_DELAY)   # троттлинг


async def start() -> None:
    global _worker_task
    await bot_client.init()
    _worker_task = asyncio.create_task(_worker())


async def stop() -> None:
    if _worker_task is not None:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
    await bot_client.close()
