"""POST /api/auth/login — вход специалиста, выдача JWT."""
import hmac
import time

import jwt
from aiohttp import web

from app.config import (
    JWT_SECRET,
    JWT_TTL_HOURS,
    CRM_LOGIN_EMAIL,
    CRM_LOGIN_PASSWORD,
)


async def login(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Неверный запрос"}, status=400)

    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    email_ok = hmac.compare_digest(email, (CRM_LOGIN_EMAIL or "").strip().lower())
    pass_ok = hmac.compare_digest(password, CRM_LOGIN_PASSWORD or "")
    if not (email_ok and pass_ok):
        return web.json_response(
            {"ok": False, "error": "Неверный логин или пароль"}, status=401
        )

    payload = {"sub": email, "exp": int(time.time()) + JWT_TTL_HOURS * 3600}
    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    return web.json_response({"ok": True, "token": token})
