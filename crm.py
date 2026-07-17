"""CRM-панель рассылок, встроенная в веб-сервер бота (маршруты под /crm).

Переиспользует пул БД (db.py) и MAX-клиент бота — отдельный сервис/БД/токен не нужны.
Очередь рассылки — внутри процесса (asyncio), с троттлингом; статусы пишутся в
схему crm и опрашиваются панелью. Панель включается, только если задан логин/пароль
и JWT-секрет (config.crm_enabled()).
"""
import asyncio
import html
import hmac
import json
import logging
import time
import uuid
from pathlib import Path

import jwt
from aiohttp import web

import db
from config import (
    JWT_SECRET, JWT_TTL_HOURS, CRM_LOGIN_EMAIL, CRM_LOGIN_PASSWORD,
    SEND_RATE_PER_SEC,
)
from max_client import MaxClient, MaxAPIError

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "webapp" / "crm"
_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}

# ---------- Внутрипроцессная рассылка ----------

_client: MaxClient | None = None
_queue: "asyncio.Queue[tuple[str, int]]" = asyncio.Queue()
_payloads: dict[str, dict] = {}   # broadcast_id -> {caption, kind, filename, data, token}
_worker_task: asyncio.Task | None = None
_DELAY = 1.0 / SEND_RATE_PER_SEC


async def _send_text(user_id: int, text: str) -> str:
    try:
        await _client.send_message(user_id=user_id, text=text, fmt="html")
        return "sent"
    except MaxAPIError as e:
        if e.status == 429:
            await asyncio.sleep(2)
            try:
                await _client.send_message(user_id=user_id, text=text, fmt="html")
                return "sent"
            except MaxAPIError as e2:
                return "blocked" if e2.status == 403 else "failed"
        return "blocked" if e.status == 403 else "failed"
    except Exception as e:
        logger.warning("CRM отправка %s: %s", user_id, e)
        return "failed"


async def _send_media(user_id: int, payload: dict) -> str:
    kind = payload["kind"]                       # 'photo' | 'document'
    upload_kind = "image" if kind == "photo" else "file"
    attach_type = "image" if kind == "photo" else "file"
    token = payload.get("token")
    if not token:
        token = await _client.upload_media_token(
            upload_kind, payload["data"], payload.get("filename") or "file"
        )
        if not token:
            return "failed"
        payload["token"] = token   # кэшируем для остальных получателей
    attachments = [{"type": attach_type, "payload": {"token": token}}]
    caption = payload.get("caption") or ""
    try:
        await _client.send_message(user_id=user_id, text=caption, fmt="html",
                                   attachments=attachments)
        return "sent"
    except MaxAPIError as e:
        return "blocked" if e.status == 403 else "failed"
    except Exception as e:
        logger.warning("CRM отправка media %s: %s", user_id, e)
        return "failed"


async def _handle(broadcast_id: str, user_id: int) -> None:
    payload = _payloads.get(broadcast_id)
    if payload is None:
        await db.crm_update_log_status(broadcast_id, user_id, "failed")
        return
    if (payload.get("kind") or "text") == "text":
        status = await _send_text(user_id, payload.get("caption") or "")
    else:
        status = await _send_media(user_id, payload)
    if status == "blocked":
        await db.crm_mark_blocked(user_id)
    await db.crm_update_log_status(broadcast_id, user_id, status)


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
        await asyncio.sleep(_DELAY)


async def start_worker(client: MaxClient) -> None:
    global _client, _worker_task
    _client = client
    _worker_task = asyncio.create_task(_worker())


async def stop_worker() -> None:
    if _worker_task is not None:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass


# ---------- Авторизация (JWT) ----------

_PUBLIC_API = {"/crm/api/auth/login"}


@web.middleware
async def crm_jwt_middleware(request: web.Request, handler):
    """Защищает /crm/api/* (кроме логина). Остальные пути — мимо (mini app и т.п.)."""
    path = request.path
    if not path.startswith("/crm/api/") or path in _PUBLIC_API:
        return await handler(request)
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    try:
        jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return web.json_response({"ok": False, "error": "Требуется авторизация"}, status=401)
    return await handler(request)


async def handle_login(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Неверный запрос"}, status=400)
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    email_ok = hmac.compare_digest(email, (CRM_LOGIN_EMAIL or "").strip().lower())
    pass_ok = hmac.compare_digest(password, CRM_LOGIN_PASSWORD or "")
    if not (email_ok and pass_ok):
        return web.json_response({"ok": False, "error": "Неверный логин или пароль"}, status=401)
    payload = {"sub": email, "exp": int(time.time()) + JWT_TTL_HOURS * 3600}
    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    return web.json_response({"ok": True, "token": token})


# ---------- Сегменты ----------

async def handle_preview(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Неверный запрос"}, status=400)
    count = await db.crm_count_users(data.get("filters") or {})
    return web.json_response({"ok": True, "count": count})


async def handle_med_ids(request: web.Request) -> web.Response:
    query = (request.query.get("q") or "").strip()
    med_ids = await (db.crm_search_med_ids(query) if query else db.crm_list_med_ids())
    return web.json_response({"ok": True, "med_ids": med_ids})


async def handle_users(request: web.Request) -> web.Response:
    query = (request.query.get("q") or "").strip()
    users = await (db.crm_search_users(query) if query else db.crm_list_users())
    return web.json_response({"ok": True, "users": users})


async def handle_user_detail(request: web.Request) -> web.Response:
    try:
        uid = int(request.match_info["id"])
    except (ValueError, KeyError):
        return web.json_response({"ok": False, "error": "Неверный ID"}, status=400)
    user = await db.crm_get_user(uid)
    if user is None:
        return web.json_response({"ok": False, "error": "Пользователь не найден"}, status=404)
    return web.json_response({"ok": True, "user": user})


# ---------- Рассылка ----------

def _blocks_to_html(blocks) -> str:
    """Блоки конструктора -> HTML для сообщения MAX (title КАПСОМ жирным и т.д.)."""
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


async def handle_broadcast(request: web.Request) -> web.Response:
    post = await request.post()
    try:
        filters = json.loads(post.get("filters") or "{}")
    except (json.JSONDecodeError, TypeError):
        filters = {}

    blocks_raw = post.get("blocks")
    if blocks_raw:
        try:
            text = _blocks_to_html(json.loads(blocks_raw))
        except (json.JSONDecodeError, TypeError, AttributeError):
            text = ""
    else:
        text = (post.get("text") or "").strip()

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
            {"ok": False, "error": "Добавьте текст или прикрепите файл"}, status=400)

    user_ids = await db.crm_get_user_ids(filters)
    if not user_ids:
        return web.json_response(
            {"ok": False, "error": "Под фильтр не попал ни один получатель"}, status=400)

    broadcast_id = uuid.uuid4().hex
    _payloads[broadcast_id] = {
        "caption": text, "kind": kind, "filename": filename, "data": data, "token": None,
    }
    await db.crm_create_pending_logs(broadcast_id, user_ids)
    for uid in user_ids:
        await _queue.put((broadcast_id, uid))

    return web.json_response(
        {"ok": True, "broadcast_id": broadcast_id, "queued": len(user_ids)})


async def handle_status(request: web.Request) -> web.Response:
    broadcast_id = request.match_info["id"]
    counts = await db.crm_broadcast_status(broadcast_id)
    return web.json_response({"ok": True, "broadcast_id": broadcast_id, **counts})


# ---------- Регистрация маршрутов ----------

def _static(name: str):
    async def handler(_request: web.Request) -> web.Response:
        resp = web.FileResponse(WEB_DIR / name)
        resp.headers["Cache-Control"] = "no-cache"
        return resp
    return handler


def register_routes(app: web.Application) -> None:
    """Добавляет middleware и маршруты /crm/* в существующее приложение бота."""
    app.middlewares.append(crm_jwt_middleware)
    app.router.add_get("/crm", lambda r: web.HTTPFound("/crm/"))
    app.router.add_get("/crm/", _static("index.html"))
    app.router.add_get("/crm/panel.js", _static("panel.js"))
    app.router.add_get("/crm/panel.css", _static("panel.css"))
    app.router.add_post("/crm/api/auth/login", handle_login)
    app.router.add_post("/crm/api/segments/preview", handle_preview)
    app.router.add_get("/crm/api/segments/med-ids", handle_med_ids)
    app.router.add_get("/crm/api/segments/users", handle_users)
    app.router.add_get("/crm/api/segments/users/{id}", handle_user_detail)
    app.router.add_post("/crm/api/broadcast", handle_broadcast)
    app.router.add_get("/crm/api/broadcast/{id}/status", handle_status)
