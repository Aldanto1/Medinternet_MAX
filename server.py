"""Веб-сервер mini app: отдаёт страницы и принимает данные из MAX WebApp."""
import asyncio
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web

import ai_client
import db
import link_token
from config import BOT_TOKEN, WEBAPP_HOST, WEBAPP_PORT, WEBAPP_VERSION, webapp_url

logger = logging.getLogger(__name__)

WEBAPP_DIR = Path(__file__).resolve().parent / "webapp"

# Аварийный обход проверки подписи для локальной отладки mini app.
# ⚠️ В проде держите выключенным (не задавайте переменную).
SKIP_INITDATA_CHECK = (os.getenv("SKIP_INITDATA_CHECK") or "").strip() in ("1", "true", "yes")


def validate_init_data(init_data: str) -> dict | None:
    """Проверяет подпись MAX WebApp.initData.

    ⚠️ ТРЕБУЕТ ПРОВЕРКИ. В источниках алгоритм MAX описан как «идентичный
    Telegram» (data_check_string + HMAC-SHA256 с ключом на основе токена бота).
    Реализовано именно так. Если модерация/боевой токен покажут иную схему —
    правьте здесь (в доке MAX Bridge встречается вариант
    HMAC_SHA256(authDate + phone + userId, botToken)).

    Возвращает разобранные поля при валидной подписи, иначе None.
    """
    if not init_data:
        return None
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    except ValueError:
        return None

    if SKIP_INITDATA_CHECK:
        parsed.pop("hash", None)
        return parsed

    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed, received_hash):
        return None
    return parsed


async def handle_register(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return web.json_response({"ok": False, "error": "Неверный формат запроса"}, status=400)

    parsed = validate_init_data(body.get("initData", ""))
    if parsed is None:
        return web.json_response(
            {"ok": False, "error": "Проверка MAX не пройдена"}, status=403
        )

    try:
        max_user = json.loads(parsed.get("user", "{}"))
    except json.JSONDecodeError:
        max_user = {}
    user_id = max_user.get("id")
    if not user_id:
        return web.json_response(
            {"ok": False, "error": "Нет данных пользователя MAX"}, status=400
        )

    # Регистрация по MedID — число от 1 до 6 цифр (1..999999)
    med_raw = str(body.get("med_id") or "").strip()
    if not med_raw.isdigit() or not (1 <= len(med_raw) <= 6):
        return web.json_response(
            {"ok": False, "error": "MedinternetID — это число от 1 до 6 цифр"}, status=400
        )
    med_id = int(med_raw)

    was_registered = await db.user_exists(user_id)
    await db.upsert_user(
        telegram_id=user_id,
        med_id=med_id,
        username=max_user.get("username"),
    )
    logger.info("Зарегистрирован пользователь %s (MedID %s)", user_id, med_id)

    # Только при ПЕРВОЙ регистрации: удаляем стартовое приглашение и шлём поздравление
    if not was_registered:
        await _notify_registered(request.app.get("client"), user_id)

    return web.json_response({"ok": True})


_CONGRATS = (
    "🎉 Поздравляем с успешной регистрацией!\n"
    "Откройте наш Mini App для использования медицинского поисковика."
)


def _miniapp_kb() -> list | None:
    url = webapp_url()
    if not url:
        return None
    return [{
        "type": "inline_keyboard",
        "payload": {"buttons": [[{"type": "open_app", "text": "Mini App",
                                  "web_app": {"url": url}}]]},
    }]


async def _notify_registered(client, user_id: int) -> None:
    """Удаляет стартовое сообщение и отправляет поздравление после регистрации."""
    if client is None:
        return
    prompt = await db.get_start_prompt(user_id)
    if prompt:
        try:
            await client.delete_message(prompt["message_id"])
        except Exception as e:
            logger.info("Не удалось удалить стартовое сообщение %s: %s", user_id, e)
        await db.delete_start_prompt(user_id)
    try:
        await client.send_message(user_id=user_id, text=_CONGRATS, fmt="html",
                                  attachments=_miniapp_kb())
    except Exception as e:
        logger.warning("Не удалось отправить поздравление %s: %s", user_id, e)


async def _authenticated_user(request: web.Request):
    """Разбирает и проверяет initData из тела запроса.

    Возвращает (max_user_dict, body, None) при успехе или (None, None, web.Response).
    """
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None, None, web.json_response(
            {"ok": False, "error": "Неверный формат запроса"}, status=400
        )
    parsed = validate_init_data(body.get("initData", ""))
    if parsed is None:
        return None, None, web.json_response(
            {"ok": False, "error": "Проверка MAX не пройдена"}, status=403
        )
    try:
        max_user = json.loads(parsed.get("user", "{}"))
    except json.JSONDecodeError:
        max_user = {}
    if not max_user.get("id"):
        return None, None, web.json_response(
            {"ok": False, "error": "Нет данных пользователя MAX"}, status=400
        )
    return max_user, body, None


async def handle_me(request: web.Request) -> web.Response:
    """Статус пользователя: регистрация, доступность ИИ и данные профиля."""
    max_user, _body, err = await _authenticated_user(request)
    if err is not None:
        return err

    await db.touch_bot_action(max_user["id"])  # открытие Mini App — тоже действие в боте
    row = await db.get_user(max_user["id"])
    profile = None
    if row is not None:
        created = row["created_at"]
        profile = {
            "full_name": row["full_name"],
            "specialty": row["specialty"],
            "position": row["position"],
            "created_at": created.isoformat() if created else None,
            "tariff": "Обычный",
        }

    return web.json_response({
        "ok": True,
        "registered": row is not None,
        "ai_enabled": ai_client.is_configured(),
        "user": profile,
    })


async def handle_ai_message(request: web.Request) -> web.Response:
    """Отправляет вопрос пользователя в RX Code AI и возвращает ответ."""
    max_user, body, err = await _authenticated_user(request)
    if err is not None:
        return err

    if not ai_client.is_configured():
        return web.json_response(
            {"ok": False, "error": "Нейросеть не настроена"}, status=503
        )

    message = (body.get("message") or "").strip()
    if not message:
        return web.json_response({"ok": False, "error": "Пустое сообщение"}, status=400)

    uid = max_user["id"]
    await db.touch_search(uid)
    try:
        chat_id = await db.get_ai_chat_id(uid)
        if not chat_id:
            chat_id = await ai_client.create_session(uid)
            await db.set_ai_chat_id(uid, chat_id)
        try:
            answer = await ai_client.send_message(chat_id, message)
        except ai_client.SessionNotFound:
            chat_id = await ai_client.create_session(uid)
            await db.set_ai_chat_id(uid, chat_id)
            answer = await ai_client.send_message(chat_id, message)
    except ai_client.AIError as e:
        logger.warning("Ошибка RX Code AI: %s", e)
        return web.json_response(
            {"ok": False, "error": "Нейросеть недоступна, попробуйте позже"}, status=502
        )

    return web.json_response({
        "ok": True,
        "answer_html": answer["html"],
        "answer_md": answer["markdown"],
        "sources": answer["sources"],
    })


async def handle_ai_stream(request: web.Request) -> web.Response:
    """Потоковый ответ RX Code AI (SSE) — текст появляется постепенно."""
    max_user, body, err = await _authenticated_user(request)
    if err is not None:
        return err
    if not ai_client.is_configured():
        return web.json_response({"ok": False, "error": "Нейросеть не настроена"}, status=503)
    message = (body.get("message") or "").strip()
    if not message:
        return web.json_response({"ok": False, "error": "Пустое сообщение"}, status=400)

    uid = max_user["id"]
    await db.touch_search(uid)
    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream; charset=utf-8",
            "Cache-Control": "no-cache, no-store, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
    await resp.prepare(request)
    await resp.write(b": ok\n\n")

    async def emit(obj):
        await resp.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))

    queue: asyncio.Queue = asyncio.Queue()

    async def feed(cid):
        async for kind, value in ai_client.stream_message(cid, message):
            await queue.put(("event", (kind, value)))

    async def producer():
        try:
            chat_id = await db.get_ai_chat_id(uid)
            if not chat_id:
                chat_id = await ai_client.create_session(uid)
                await db.set_ai_chat_id(uid, chat_id)
            try:
                await feed(chat_id)
            except ai_client.SessionNotFound:
                chat_id = await ai_client.create_session(uid)
                await db.set_ai_chat_id(uid, chat_id)
                await feed(chat_id)
        except ai_client.AIError as e:
            logger.warning("Ошибка RX Code AI (stream): %s", e)
            await queue.put(("event", ("error", "Нейросеть недоступна, попробуйте позже")))
        except Exception as e:
            logger.warning("Ошибка стрима %s: %s", uid, e)
            await queue.put(("event", ("error", "Что-то пошло не так. Попробуйте позже.")))
        finally:
            await queue.put(("end", None))

    task = asyncio.create_task(producer())
    try:
        while True:
            try:
                typ, payload = await asyncio.wait_for(queue.get(), timeout=2.0)
            except asyncio.TimeoutError:
                await resp.write(b": ping\n\n")
                continue
            if typ == "end":
                break
            kind, value = payload
            await emit({"kind": kind, "value": value})
        await emit({"kind": "done"})
    except Exception as e:
        logger.warning("Ошибка отдачи стрима %s: %s", uid, e)
    finally:
        if not task.done():
            task.cancel()
    return resp


async def handle_ai_reset(request: web.Request) -> web.Response:
    """Сбрасывает текущую сессию, чтобы начать новый диалог."""
    max_user, _body, err = await _authenticated_user(request)
    if err is not None:
        return err
    await db.clear_ai_session(max_user["id"])
    return web.json_response({"ok": True})


def _file(name: str):
    async def handler(_request: web.Request) -> web.Response:
        resp = web.FileResponse(WEBAPP_DIR / name)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    return handler


def _render_index() -> str:
    html = (WEBAPP_DIR / "index.html").read_text(encoding="utf-8")
    return (
        html.replace("/style.css", f"/style.css?v={WEBAPP_VERSION}")
            .replace("/app.js", f"/app.js?v={WEBAPP_VERSION}")
    )


async def handle_index(_request: web.Request) -> web.Response:
    return web.Response(
        text=_render_index(),
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


async def handle_link(request: web.Request) -> web.Response:
    """Прототип личного кабинета medinternet.ru: выдаёт свежую одноразовую
    ссылку на бота при каждом заходе (deep-link с подписанным токеном).

    ⚠️ Формат deep-link для запуска MAX-бота с payload требует проверки.
    Здесь используется https://max.ru/<botName>?start=<token>; payload придёт
    в апдейте bot_started (поле payload).
    """
    bot_name = request.app.get("bot_name") or ""
    token = link_token.make_link_token()
    bot_deeplink = f"https://max.ru/{bot_name}?start={token}"
    html = (WEBAPP_DIR / "link.html").read_text(encoding="utf-8")
    return web.Response(
        text=html.replace("{{BOT_LINK}}", bot_deeplink),
        content_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


def build_app(client=None, bot_name: str = "") -> web.Application:
    app = web.Application()
    app["client"] = client       # нужен для уведомлений после регистрации
    app["bot_name"] = bot_name   # для deep-link на странице /link
    app.router.add_get("/", handle_index)
    app.router.add_get("/link", handle_link)
    app.router.add_get("/app.js", _file("app.js"))
    app.router.add_get("/style.css", _file("style.css"))
    app.router.add_get("/logo.png", _file("logo.png"))
    app.router.add_post("/api/register", handle_register)
    app.router.add_post("/api/me", handle_me)
    app.router.add_post("/api/ai/message", handle_ai_message)
    app.router.add_post("/api/ai/message/stream", handle_ai_stream)
    app.router.add_post("/api/ai/reset", handle_ai_reset)
    return app


async def start_webserver(client=None, bot_name: str = "") -> web.AppRunner:
    """Поднимает веб-сервер на локальном порту и возвращает runner для остановки."""
    runner = web.AppRunner(build_app(client, bot_name))
    await runner.setup()
    site = web.TCPSite(runner, WEBAPP_HOST, WEBAPP_PORT)
    await site.start()
    logger.info("Веб-сервер mini app слушает http://%s:%s", WEBAPP_HOST, WEBAPP_PORT)
    return runner
