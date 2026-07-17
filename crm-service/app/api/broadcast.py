"""Рассылка: постановка в очередь (с текстом и/или файлом) и статус."""
import html
import json
import uuid

from aiohttp import web

from app import db, broadcaster

_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


def blocks_to_html(blocks) -> str:
    """Собирает блоки конструктора в HTML-текст для сообщения MAX.

    title    → жирным КАПСОМ
    subtitle → жирным
    text     → обычный текст
    link     → кликабельная ссылка
    Между блоками — пустая строка (отступ). Контент экранируется.
    """
    parts = []
    for b in blocks if isinstance(blocks, list) else []:
        btype = b.get("type")
        text = (b.get("text") or "").strip()
        if btype == "title" and text:
            parts.append(f"<b>{html.escape(text.upper())}</b>")
        elif btype == "subtitle" and text:
            parts.append(f"<b>{html.escape(text)}</b>")
        elif btype == "text" and text:
            parts.append(html.escape(text))
        elif btype == "link":
            url = (b.get("url") or "").strip()
            if url:
                label = html.escape(text or url)
                parts.append(f'<a href="{html.escape(url, quote=True)}">{label}</a>')
    return "\n\n".join(parts)


async def create_broadcast(request: web.Request) -> web.Response:
    """POST /api/broadcast (multipart/form-data): filters + text + необязательный file."""
    post = await request.post()

    try:
        filters = json.loads(post.get("filters") or "{}")
    except (json.JSONDecodeError, TypeError):
        filters = {}

    # Сообщение: либо блоки конструктора, либо (для совместимости) простой text
    blocks_raw = post.get("blocks")
    if blocks_raw:
        try:
            text = blocks_to_html(json.loads(blocks_raw))
        except (json.JSONDecodeError, TypeError, AttributeError):
            text = ""
    else:
        text = (post.get("text") or "").strip()

    # Необязательный файл
    kind, filename, data = "text", None, None
    file_field = post.get("file")
    if file_field is not None and hasattr(file_field, "file"):
        data = file_field.file.read()
        if data:
            filename = file_field.filename or "file"
            ctype = (file_field.content_type or "").lower()
            kind = "photo" if ctype in _IMAGE_TYPES else "document"
        else:
            data = None

    if not text and data is None:
        return web.json_response(
            {"ok": False, "error": "Добавьте текст или прикрепите файл"}, status=400
        )

    user_ids = await db.get_telegram_ids(filters)
    if not user_ids:
        return web.json_response(
            {"ok": False, "error": "Под фильтр не попал ни один получатель"}, status=400
        )

    broadcast_id = uuid.uuid4().hex
    broadcaster.store_payload(broadcast_id, text, kind, filename, data)
    await db.create_pending_logs(broadcast_id, user_ids)
    await broadcaster.enqueue(broadcast_id, user_ids)

    return web.json_response(
        {"ok": True, "broadcast_id": broadcast_id, "queued": len(user_ids)}
    )


async def broadcast_status(request: web.Request) -> web.Response:
    """GET /api/broadcast/{id}/status — счётчики sent/failed/blocked/pending."""
    broadcast_id = request.match_info["id"]
    counts = await db.get_broadcast_status(broadcast_id)
    return web.json_response({"ok": True, "broadcast_id": broadcast_id, **counts})
