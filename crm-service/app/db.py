"""Доступ к Neon PostgreSQL для CRM-сервиса.

- Чтение таблицы public.users (только SELECT — под read-only ролью).
- Запись служебных данных в схему crm (blocked_users, broadcast_log).

Все фильтры строятся через параметризованные запросы asyncpg — никакой ручной
конкатенации значений в SQL (защита от инъекций).
"""
import re
from datetime import date, datetime

import asyncpg

from app.config import CRM_DB_URL

_pool: asyncpg.Pool | None = None


def _clean_dsn(url: str) -> str:
    """asyncpg не понимает sslmode/channel_binding в DSN — убираем их."""
    return re.sub(r"[?&](sslmode|channel_binding)=[^&]*", "", url)


async def init() -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        _clean_dsn(CRM_DB_URL), ssl="require", min_size=1, max_size=5
    )


async def close() -> None:
    if _pool is not None:
        await _pool.close()


def _parse_dt(value):
    """Принимает 'YYYY-MM-DD' или ISO-datetime, возвращает date/datetime."""
    if isinstance(value, (date, datetime)):
        return value
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return date.fromisoformat(value)


def build_where(filters: dict) -> tuple[str, list]:
    """Строит SQL WHERE и список параметров по словарю фильтров.

    Чистая функция (без обращения к БД) — удобно тестировать.
    Значения пользователя идут ТОЛЬКО в params ($1, $2, ...), не в текст SQL.

    Поддерживаемые фильтры:
      created_from / created_to — диапазон даты регистрации (created_at)
      has_email: true           — есть непустой email
      has_phone: true           — есть непустой телефон

    Заглушки (колонок нет в users — см. README):
      last_active_at_from / _to  — активность (нет поля last_active_at)
      tag / status               — сегмент по метке (нет поля)
    """
    clauses: list[str] = []
    params: list = []

    def ph(value) -> str:
        params.append(value)
        return f"${len(params)}"

    if filters.get("created_from"):
        clauses.append(f"created_at >= {ph(_parse_dt(filters['created_from']))}")
    if filters.get("created_to"):
        clauses.append(f"created_at <= {ph(_parse_dt(filters['created_to']))}")
    if filters.get("has_email") is True:
        clauses.append("email IS NOT NULL AND email <> ''")
    if filters.get("has_email") is False:
        clauses.append("(email IS NULL OR email = '')")
    if filters.get("has_phone") is True:
        clauses.append("phone IS NOT NULL AND phone <> ''")
    if filters.get("has_phone") is False:
        clauses.append("(phone IS NULL OR phone = '')")

    # Конкретные получатели по MedID (мультивыбор, legacy)
    med_ids = filters.get("med_ids")
    if med_ids:
        ids = []
        for m in med_ids:
            try:
                ids.append(int(m))
            except (ValueError, TypeError):
                pass
        if ids:
            clauses.append(f"med_id = ANY({ph(ids)})")

    # Конкретные получатели по Telegram ID (мультивыбор) —
    # основной способ адресной рассылки для deep-link регистрации.
    tg_ids = filters.get("tg_ids")
    if tg_ids:
        ids = []
        for t in tg_ids:
            try:
                ids.append(int(t))
            except (ValueError, TypeError):
                pass
        if ids:
            clauses.append(f"telegram_id = ANY({ph(ids)})")

    where = " AND ".join(clauses) if clauses else "TRUE"
    return where, params


def _user_nick(row) -> str:
    """Ник для списка получателей: имя из Telegram, иначе @username, иначе «—»."""
    full_name = (row["full_name"] or "").strip()
    if full_name:
        return full_name
    username = (row["username"] or "").strip()
    if username:
        return "@" + username
    return "—"


# Исключаем тех, кто заблокировал бота (им отправка невозможна)
_NOT_BLOCKED = "telegram_id NOT IN (SELECT telegram_id FROM crm.blocked_users)"


async def count_users(filters: dict) -> int:
    """Количество доставляемых пользователей по фильтру (для preview)."""
    assert _pool is not None, "db.init() ещё не вызван"
    where, params = build_where(filters)
    async with _pool.acquire() as conn:
        return await conn.fetchval(
            f"SELECT count(*) FROM public.users WHERE ({where}) AND {_NOT_BLOCKED}",
            *params,
        )


async def search_med_ids(query: str, limit: int = 15) -> list[int]:
    """Подсказки MedID для автодополнения: id, начинающиеся с введённых цифр."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT med_id FROM public.users
            WHERE med_id IS NOT NULL AND CAST(med_id AS TEXT) LIKE $1 || '%'
            ORDER BY med_id
            LIMIT $2
            """,
            query,
            limit,
        )
    return [r["med_id"] for r in rows]


async def list_med_ids(limit: int = 1000) -> list[int]:
    """Все зарегистрированные MedID по возрастанию (для кнопки «Список»)."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT med_id FROM public.users WHERE med_id IS NOT NULL "
            "ORDER BY med_id LIMIT $1",
            limit,
        )
    return [r["med_id"] for r in rows]


async def list_users(limit: int = 1000) -> list[dict]:
    """Зарегистрированные пользователи для списка получателей: id + ник.

    Новые сверху (по дате регистрации). Ник — имя из Telegram / @username.
    """
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT telegram_id, username, full_name FROM public.users "
            "ORDER BY created_at DESC, telegram_id LIMIT $1",
            limit,
        )
    return [{"id": r["telegram_id"], "nick": _user_nick(r)} for r in rows]


async def search_users(query: str, limit: int = 15) -> list[dict]:
    """Подсказки получателей по вводу: совпадение по Telegram ID или нику."""
    assert _pool is not None, "db.init() ещё не вызван"
    like = "%" + query + "%"
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT telegram_id, username, full_name FROM public.users
            WHERE CAST(telegram_id AS TEXT) LIKE $1 || '%'
               OR username ILIKE $2
               OR full_name ILIKE $2
            ORDER BY created_at DESC, telegram_id
            LIMIT $3
            """,
            query,
            like,
            limit,
        )
    return [{"id": r["telegram_id"], "nick": _user_nick(r)} for r in rows]


async def get_user(telegram_id: int) -> dict | None:
    """Полная карточка пользователя (все поля public.users) + признак блокировки."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT telegram_id, username, full_name, med_id, "
            "created_at, last_bot_action_at, last_search_at "
            "FROM public.users WHERE telegram_id = $1",
            telegram_id,
        )
        if row is None:
            return None
        blocked = await conn.fetchval(
            "SELECT 1 FROM crm.blocked_users WHERE telegram_id = $1", telegram_id
        )
    data = dict(row)
    # Даты/время — в ISO-строки для JSON
    for key in ("created_at", "last_bot_action_at", "last_search_at"):
        if data.get(key) is not None:
            data[key] = data[key].isoformat()
    data["blocked"] = blocked is not None
    return data


async def get_telegram_ids(filters: dict) -> list[int]:
    """Список telegram_id по фильтру (без заблокировавших) — для постановки задач."""
    assert _pool is not None, "db.init() ещё не вызван"
    where, params = build_where(filters)
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT telegram_id FROM public.users WHERE ({where}) AND {_NOT_BLOCKED}",
            *params,
        )
    return [r["telegram_id"] for r in rows]


# ---------- Служебная схема crm (запись) ----------

async def create_pending_logs(broadcast_id: str, telegram_ids: list[int]) -> None:
    """Создаёт по строке-«pending» на каждого получателя рассылки."""
    assert _pool is not None, "db.init() ещё не вызван"
    rows = [(broadcast_id, tid, "pending") for tid in telegram_ids]
    async with _pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO crm.broadcast_log (broadcast_id, telegram_id, status) "
            "VALUES ($1, $2, $3)",
            rows,
        )


async def update_log_status(broadcast_id: str, telegram_id: int, status: str) -> None:
    """Обновляет статус доставки конкретному пользователю."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE crm.broadcast_log SET status = $3 "
            "WHERE broadcast_id = $1 AND telegram_id = $2",
            broadcast_id,
            telegram_id,
            status,
        )


async def mark_blocked(telegram_id: int) -> None:
    """Помечает пользователя как заблокировавшего бота."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO crm.blocked_users (telegram_id) VALUES ($1) "
            "ON CONFLICT (telegram_id) DO NOTHING",
            telegram_id,
        )


async def get_broadcast_status(broadcast_id: str) -> dict:
    """Счётчики по статусам рассылки: sent/failed/blocked/pending."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT status, count(*) AS n FROM crm.broadcast_log "
            "WHERE broadcast_id = $1 GROUP BY status",
            broadcast_id,
        )
    counts = {"sent": 0, "failed": 0, "blocked": 0, "pending": 0}
    for r in rows:
        counts[r["status"]] = r["n"]
    counts["total"] = sum(counts.values())
    return counts
