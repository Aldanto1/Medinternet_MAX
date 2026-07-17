import os
import time
from pathlib import Path
from dotenv import load_dotenv

# Загружаем переменные из env/.env
ENV_PATH = Path(__file__).resolve().parent / "env" / ".env"
load_dotenv(dotenv_path=ENV_PATH)

# ---------- MAX Bot ----------
# Токен бота из кабинета MAX: Чат-боты → Перейти → Расширенные настройки → Настроить.
# .strip() обязателен: лишние пробелы/переносы строк (частый артефакт вставки
# токена в переменные окружения) ломают заголовок Authorization —
# aiohttp падает с «Forbidden control character detected in headers».
BOT_TOKEN = os.getenv("BOT_TOKEN")
if BOT_TOKEN:
    BOT_TOKEN = BOT_TOKEN.strip()

# Базовый адрес MAX Bot API. В документации/статьях встречаются разные хосты
# (botapi.max.ru и platform-api2.max.ru) — вынесено в переменную, чтобы можно
# было переключить без правки кода при 401/404.
MAX_API_BASE = (os.getenv("MAX_API_BASE") or "https://botapi.max.ru").strip().rstrip("/")

# Способ авторизации: по умолчанию токен идёт в заголовке Authorization.
# Если ваша версия API принимает только query-параметр access_token — задайте
# MAX_AUTH_QUERY=1 в .env.
MAX_AUTH_QUERY = (os.getenv("MAX_AUTH_QUERY") or "").strip() in ("1", "true", "yes")

# Имя бота в MAX (без @) — нужно для ссылок вида https://max.ru/<botName>?startapp=...
# Если не задано, попробуем взять из /me при старте.
BOT_NAME = (os.getenv("BOT_NAME") or "").strip().lstrip("@")

# ---------- База данных (Neon / PostgreSQL) ----------
DATABASE_URL = os.getenv("DATABASE_URL")

# ---------- Mini App (веб-приложение) ----------
# Публичный HTTPS-адрес, по которому MAX открывает mini app.
WEBAPP_URL = os.getenv("WEBAPP_URL")
if WEBAPP_URL:
    WEBAPP_URL = WEBAPP_URL.strip().rstrip("/")
    if not WEBAPP_URL.startswith(("http://", "https://")):
        WEBAPP_URL = "https://" + WEBAPP_URL

# Адрес и порт веб-сервера mini app.
WEBAPP_HOST = os.getenv("WEBAPP_HOST", "0.0.0.0")
WEBAPP_PORT = int(os.getenv("PORT") or os.getenv("WEBAPP_PORT") or "8080")

# Версия mini app — меняется при каждом запуске (деплое). Сброс кэша: подставляется
# в URL кнопок и в ссылки на style.css/app.js.
WEBAPP_VERSION = str(int(time.time()))


def webapp_url():
    """WEBAPP_URL с параметром версии — чтобы MAX грузил свежий mini app после деплоя."""
    if not WEBAPP_URL:
        return None
    sep = "&" if "?" in WEBAPP_URL else "?"
    return f"{WEBAPP_URL}{sep}v={WEBAPP_VERSION}"


# ---------- API сервера Medinternet ----------
API_SERVER_URL = os.getenv("API_SERVER_URL")
API_SERVER_KEY = os.getenv("API_SERVER_KEY")

# ---------- Нейросеть RX Code AI (медицинский поисковик в mini app) ----------
# NEURO_API_URL — базовый адрес API БЕЗ /api в конце (код сам добавляет /api/chats).
NEURO_API_URL = os.getenv("NEURO_API_URL")
if NEURO_API_URL:
    NEURO_API_URL = NEURO_API_URL.strip().rstrip("/")
NEURO_API_KEY = os.getenv("NEURO_API_KEY")
if NEURO_API_KEY:
    NEURO_API_KEY = NEURO_API_KEY.strip()
NEURO_CHANNEL = os.getenv("NEURO_CHANNEL", "michat")


# ---------- CRM-панель рассылок (встроена в этот же сервис, путь /crm) ----------
# Панель включается, только если заданы все три переменные ниже. Базу и токен
# CRM переиспользует у бота (DATABASE_URL, BOT_TOKEN) — отдельные не нужны.
JWT_SECRET = os.getenv("JWT_SECRET")
JWT_TTL_HOURS = int(os.getenv("JWT_TTL_HOURS", "12"))
CRM_LOGIN_EMAIL = os.getenv("CRM_LOGIN_EMAIL")
CRM_LOGIN_PASSWORD = os.getenv("CRM_LOGIN_PASSWORD")
# Троттлинг рассылки (сообщений в секунду; лимит MAX ~30/сек)
SEND_RATE_PER_SEC = float(os.getenv("SEND_RATE_PER_SEC", "25"))


def crm_enabled() -> bool:
    """CRM-панель активна, только если заданы логин/пароль и секрет JWT."""
    return bool(JWT_SECRET and CRM_LOGIN_EMAIL and CRM_LOGIN_PASSWORD)


def validate_config():
    """Проверяет, что все обязательные переменные заданы."""
    missing = []
    if not BOT_TOKEN or BOT_TOKEN == "your_bot_token_here":
        missing.append("BOT_TOKEN")
    if not DATABASE_URL:
        missing.append("DATABASE_URL")
    if missing:
        raise ValueError(
            f"Отсутствуют обязательные переменные окружения: {', '.join(missing)}.\n"
            f"Заполните файл: {ENV_PATH}"
        )
