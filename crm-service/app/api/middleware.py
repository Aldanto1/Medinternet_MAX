"""JWT-мидлварь: защищает все /api/* кроме /api/auth/login."""
import jwt
from aiohttp import web

from app.config import JWT_SECRET

_PUBLIC = {"/api/auth/login"}


@web.middleware
async def jwt_middleware(request: web.Request, handler):
    path = request.path
    # Не-API пути (страница панели, статика) и логин — без токена
    if not path.startswith("/api/") or path in _PUBLIC:
        return await handler(request)

    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return web.json_response(
            {"ok": False, "error": "Требуется авторизация"}, status=401
        )
    request["user"] = payload.get("sub")
    return await handler(request)
