"""Конфигурация CRM-сервиса из переменных окружения (crm-service/.env)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)

# Neon: строка подключения (read-only роль для чтения users + запись в схему crm,
# либо тот же DATABASE_URL, что у бота).
CRM_DB_URL = os.getenv("CRM_DB_URL")

# Токен MAX-бота — им шлём сообщения пользователям (тот же, что у основного бота).
# .strip(): лишний перенос строки ломает заголовок Authorization в aiohttp.
MAX_BOT_TOKEN = (os.getenv("MAX_BOT_TOKEN") or os.getenv("MAIN_BOT_TOKEN") or "").strip()

# Базовый адрес MAX Bot API (при 401/404 попробуйте platform-api2.max.ru).
MAX_API_BASE = (os.getenv("MAX_API_BASE") or "https://botapi.max.ru").strip().rstrip("/")
# Токен query-параметром access_token вместо заголовка Authorization.
MAX_AUTH_QUERY = (os.getenv("MAX_AUTH_QUERY") or "").strip() in ("1", "true", "yes")

# JWT-аутентификация специалистов
JWT_SECRET = os.getenv("JWT_SECRET")
JWT_TTL_HOURS = int(os.getenv("JWT_TTL_HOURS", "12"))

# Учётные данные специалиста (MVP: сверяем с .env, без хеширования)
CRM_LOGIN_EMAIL = os.getenv("CRM_LOGIN_EMAIL")
CRM_LOGIN_PASSWORD = os.getenv("CRM_LOGIN_PASSWORD")

# Троттлинг рассылки (сообщений в секунду; лимит MAX Bot API ~30/сек)
SEND_RATE_PER_SEC = float(os.getenv("SEND_RATE_PER_SEC", "25"))

# HTTP-сервер API
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("PORT") or os.getenv("API_PORT") or "8090")


def validate(require_all: bool = True) -> None:
    """Проверяет обязательные переменные."""
    missing = []
    if not CRM_DB_URL:
        missing.append("CRM_DB_URL")
    if require_all:
        for name, val in [
            ("MAX_BOT_TOKEN", MAX_BOT_TOKEN),
            ("JWT_SECRET", JWT_SECRET),
            ("CRM_LOGIN_EMAIL", CRM_LOGIN_EMAIL),
            ("CRM_LOGIN_PASSWORD", CRM_LOGIN_PASSWORD),
        ]:
            if not val:
                missing.append(name)
    if missing:
        raise ValueError(
            f"Отсутствуют переменные окружения: {', '.join(missing)}. "
            f"Заполните {ENV_PATH}"
        )
