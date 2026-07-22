"""Работа с базой данных Neon (PostgreSQL) через asyncpg."""
import re

import asyncpg

from config import DATABASE_URL

_pool: asyncpg.Pool | None = None


def _clean_dsn(url: str) -> str:
    """asyncpg не понимает libpq-параметры sslmode/channel_binding в строке
    подключения — убираем их, а SSL включаем отдельным аргументом."""
    return re.sub(r"[?&](sslmode|channel_binding)=[^&]*", "", url)


async def init() -> None:
    """Создаёт пул соединений и таблицу users, если её ещё нет."""
    global _pool
    _pool = await asyncpg.create_pool(
        _clean_dsn(DATABASE_URL),
        ssl="require",
        min_size=1,
        max_size=5,
    )
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id BIGINT PRIMARY KEY,
                username    TEXT,
                full_name   TEXT,
                phone       TEXT,
                email       TEXT,
                birth_date  DATE,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        # Регистрация теперь по MedID. Старые поля оставляем nullable
        # (совместимость с CRM, который читает email/phone/full_name).
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS med_id INTEGER")
        await conn.execute("ALTER TABLE users ALTER COLUMN full_name DROP NOT NULL")
        # Профиль (заполняется позже из БД medinternet)
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS specialty TEXT")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS position TEXT")
        # Активность: последнее действие в боте и последний запрос в поисковике
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_bot_action_at TIMESTAMPTZ")
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_search_at TIMESTAMPTZ")
        # Активен ли аккаунт. «Выйти из аккаунта» ставит FALSE (данные сохраняются),
        # повторная регистрация возвращает TRUE.
        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE")
        # Одноразовые токены deep-link регистрации
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS link_tokens (
                token   TEXT PRIMARY KEY,
                used_by BIGINT,
                used_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        # Сессия чата с RX Code AI (одна активная сессия на пользователя).
        # RX Code AI хранит контекст на своей стороне — держим только SessionId.
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_sessions (
                telegram_id BIGINT PRIMARY KEY,
                chat_id     TEXT NOT NULL,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        # Ссылка на текущий «чат» пользователя (см. таблицу chats).
        await conn.execute("ALTER TABLE ai_sessions ADD COLUMN IF NOT EXISTS chat_row_id BIGINT")
        # История переписки: чаты пользователя и их сообщения.
        # Название чата = первый запрос пользователя (title).
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chats (
                id          BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                title       TEXT,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chats_user ON chats (telegram_id, created_at DESC)"
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                id         BIGSERIAL PRIMARY KEY,
                chat_id    BIGINT NOT NULL,
                role       TEXT NOT NULL,   -- user / ai
                content    TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_messages ON chat_messages (chat_id, id)"
        )
        # Стартовое сообщение бота (с приглашением зарегистрироваться) —
        # чтобы удалить его после успешной регистрации.
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS start_prompts (
                telegram_id BIGINT PRIMARY KEY,
                chat_id     BIGINT NOT NULL,
                message_id  TEXT   NOT NULL,   -- в MAX идентификатор сообщения (mid) — строка
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        # Текущее «главное» сообщение бота у пользователя — чтобы в чате всегда было
        # не больше одного (прежнее удаляем при показе нового).
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS main_messages (
                telegram_id BIGINT PRIMARY KEY,
                message_id  TEXT NOT NULL,
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        # Оценки ответов поисковика (лайк/дизлайк из mini app).
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_feedback (
                id          BIGSERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                question    TEXT,
                rating      TEXT NOT NULL,   -- like / dislike
                created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        # Служебная схема CRM-панели рассылок (создаётся автоматически, без ручной миграции).
        await conn.execute("CREATE SCHEMA IF NOT EXISTS crm")
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS crm.blocked_users (
                telegram_id BIGINT PRIMARY KEY,
                blocked_at  TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS crm.broadcast_log (
                id           BIGSERIAL PRIMARY KEY,
                broadcast_id TEXT       NOT NULL,
                telegram_id  BIGINT     NOT NULL,
                status       TEXT       NOT NULL,   -- sent / failed / blocked / pending
                created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_broadcast_log_bid ON crm.broadcast_log (broadcast_id)"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_broadcast_log_status "
            "ON crm.broadcast_log (broadcast_id, status)"
        )


async def close() -> None:
    """Закрывает пул соединений."""
    if _pool is not None:
        await _pool.close()


async def upsert_user(telegram_id: int, med_id: int, username: str | None = None) -> None:
    """Создаёт или обновляет пользователя по telegram_id (регистрация по MedID)."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (telegram_id, med_id, username, active)
            VALUES ($1, $2, $3, TRUE)
            ON CONFLICT (telegram_id) DO UPDATE SET
                med_id     = EXCLUDED.med_id,
                username   = EXCLUDED.username,
                active     = TRUE,
                updated_at = now()
            """,
            telegram_id,
            med_id,
            username,
        )


async def register_user(
    telegram_id: int, username: str | None = None, full_name: str | None = None
) -> None:
    """Регистрация по deep-link: создаёт запись пользователя.

    Сохраняем ник (@username) и отображаемое имя из Telegram (full_name) —
    их показывает и кабинет мини-аппа, и список получателей в CRM.
    Специальность/должность заполнятся позже из БД medinternet.
    """
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (telegram_id, username, full_name, last_bot_action_at, active)
            VALUES ($1, $2, $3, now(), TRUE)
            ON CONFLICT (telegram_id) DO UPDATE SET
                username           = EXCLUDED.username,
                full_name          = EXCLUDED.full_name,
                active             = TRUE,
                last_bot_action_at = now(),
                updated_at         = now()
            """,
            telegram_id,
            username,
            full_name,
        )


async def touch_bot_action(telegram_id: int) -> None:
    """Отмечает время последнего действия пользователя в боте (для аналитики в CRM)."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET last_bot_action_at = now() WHERE telegram_id = $1",
            telegram_id,
        )


async def touch_search(telegram_id: int) -> None:
    """Отмечает время последнего запроса в поисковике (и заодно действия в боте)."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET last_search_at = now(), last_bot_action_at = now() "
            "WHERE telegram_id = $1",
            telegram_id,
        )


async def claim_link_token(token: str, telegram_id: int) -> bool:
    """Атомарно «гасит» токен. True — если токен ещё не был использован (первый раз)."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        row = await conn.fetchval(
            """
            INSERT INTO link_tokens (token, used_by) VALUES ($1, $2)
            ON CONFLICT (token) DO NOTHING
            RETURNING token
            """,
            token,
            telegram_id,
        )
    return row is not None


async def get_user(telegram_id: int):
    """Возвращает запись пользователя или None."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM users WHERE telegram_id = $1", telegram_id
        )


async def user_exists(telegram_id: int) -> bool:
    """True, если пользователь зарегистрирован И активен (не вышел из аккаунта)."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        row = await conn.fetchval(
            "SELECT 1 FROM users WHERE telegram_id = $1 AND active", telegram_id
        )
        return row is not None


async def logout_user(telegram_id: int) -> None:
    """«Выйти из аккаунта»: деактивирует запись, НЕ удаляя данные."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET active = FALSE, updated_at = now() WHERE telegram_id = $1",
            telegram_id,
        )


async def get_ai_chat_id(telegram_id: int) -> str | None:
    """Возвращает id активной сессии чата с нейросетью или None."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT chat_id FROM ai_sessions WHERE telegram_id = $1", telegram_id
        )


async def set_ai_chat_id(telegram_id: int, chat_id: str) -> None:
    """Сохраняет/обновляет id сессии чата с нейросетью для пользователя."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO ai_sessions (telegram_id, chat_id)
            VALUES ($1, $2)
            ON CONFLICT (telegram_id) DO UPDATE SET
                chat_id    = EXCLUDED.chat_id,
                created_at = now()
            """,
            telegram_id,
            chat_id,
        )


async def clear_ai_session(telegram_id: int) -> None:
    """Удаляет активную сессию чата, чтобы начать новый диалог."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM ai_sessions WHERE telegram_id = $1", telegram_id
        )


async def start_chat(telegram_id: int, rx_chat_id: str, title: str) -> int:
    """Создаёт новый чат (title = первый запрос) и делает его активным.

    Возвращает id созданного чата (chat_row_id). RX-сессия привязывается к чату
    через ai_sessions.chat_row_id.
    """
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        chat_row_id = await conn.fetchval(
            "INSERT INTO chats (telegram_id, title) VALUES ($1, $2) RETURNING id",
            telegram_id, (title or "")[:200],
        )
        await conn.execute(
            """
            INSERT INTO ai_sessions (telegram_id, chat_id, chat_row_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (telegram_id) DO UPDATE SET
                chat_id     = EXCLUDED.chat_id,
                chat_row_id = EXCLUDED.chat_row_id,
                created_at  = now()
            """,
            telegram_id, rx_chat_id, chat_row_id,
        )
    return chat_row_id


async def get_active_chat(telegram_id: int) -> int | None:
    """id текущего чата пользователя (или None)."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT chat_row_id FROM ai_sessions WHERE telegram_id = $1", telegram_id
        )


async def add_chat_message(chat_row_id: int, role: str, content: str) -> None:
    """Добавляет сообщение (user/ai) в чат."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO chat_messages (chat_id, role, content) VALUES ($1, $2, $3)",
            chat_row_id, role, content,
        )


async def list_chats(telegram_id: int, limit: int = 100) -> list[dict]:
    """Список чатов пользователя (новые сверху): id, название, дата."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, title, created_at FROM chats "
            "WHERE telegram_id = $1 ORDER BY created_at DESC LIMIT $2",
            telegram_id, limit,
        )
    return [
        {"id": r["id"], "title": r["title"],
         "created_at": r["created_at"].isoformat() if r["created_at"] else None}
        for r in rows
    ]


async def get_chat_messages(telegram_id: int, chat_row_id: int) -> list[dict]:
    """Сообщения чата (проверяя, что чат принадлежит пользователю)."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT m.role, m.content
            FROM chat_messages m
            JOIN chats c ON c.id = m.chat_id
            WHERE m.chat_id = $1 AND c.telegram_id = $2
            ORDER BY m.id
            """,
            chat_row_id, telegram_id,
        )
    return [{"role": r["role"], "content": r["content"]} for r in rows]


async def add_ai_feedback(telegram_id: int, question: str, rating: str) -> None:
    """Сохраняет оценку ответа поисковика (like/dislike)."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO ai_feedback (telegram_id, question, rating) VALUES ($1, $2, $3)",
            telegram_id, question, rating,
        )


async def set_start_prompt(telegram_id: int, chat_id: int, message_id: str) -> None:
    """Запоминает id стартового сообщения (приглашения к регистрации)."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO start_prompts (telegram_id, chat_id, message_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (telegram_id) DO UPDATE SET
                chat_id    = EXCLUDED.chat_id,
                message_id = EXCLUDED.message_id,
                created_at = now()
            """,
            telegram_id,
            chat_id,
            message_id,
        )


async def get_start_prompt(telegram_id: int):
    """Возвращает запись стартового сообщения (chat_id, message_id) или None."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT chat_id, message_id FROM start_prompts WHERE telegram_id = $1",
            telegram_id,
        )


async def delete_start_prompt(telegram_id: int) -> None:
    """Удаляет запись о стартовом сообщении."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM start_prompts WHERE telegram_id = $1", telegram_id
        )


async def get_main_message(telegram_id: int) -> str | None:
    """id текущего главного сообщения бота у пользователя (или None)."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT message_id FROM main_messages WHERE telegram_id = $1", telegram_id
        )


async def set_main_message(telegram_id: int, message_id: str) -> None:
    """Запоминает id нового главного сообщения (перезаписывает прежнее)."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO main_messages (telegram_id, message_id, updated_at)
            VALUES ($1, $2, now())
            ON CONFLICT (telegram_id) DO UPDATE SET
                message_id = EXCLUDED.message_id,
                updated_at = now()
            """,
            telegram_id, message_id,
        )


async def clear_main_message(telegram_id: int) -> None:
    """Забывает id главного сообщения (после его удаления)."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM main_messages WHERE telegram_id = $1", telegram_id
        )


# ============================================================================
# CRM-панель рассылок: выборка получателей, карточка пользователя, лог рассылки.
# Работает поверх того же пула и таблицы users (telegram_id хранит MAX user_id).
# ============================================================================

from datetime import date, datetime  # noqa: E402


def _crm_parse_dt(value):
    if isinstance(value, (date, datetime)):
        return value
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return date.fromisoformat(value)


def crm_build_where(filters: dict) -> tuple[str, list]:
    """SQL WHERE + параметры по фильтрам сегмента (значения только в params).

    Поддерживает: created_from/created_to, has_email, has_phone,
    med_ids (мультивыбор), tg_ids (мультивыбор MAX ID — основной способ).
    """
    clauses: list[str] = []
    params: list = []

    def ph(value) -> str:
        params.append(value)
        return f"${len(params)}"

    if filters.get("created_from"):
        clauses.append(f"created_at >= {ph(_crm_parse_dt(filters['created_from']))}")
    if filters.get("created_to"):
        clauses.append(f"created_at <= {ph(_crm_parse_dt(filters['created_to']))}")
    if filters.get("has_email") is True:
        clauses.append("email IS NOT NULL AND email <> ''")
    if filters.get("has_email") is False:
        clauses.append("(email IS NULL OR email = '')")
    if filters.get("has_phone") is True:
        clauses.append("phone IS NOT NULL AND phone <> ''")
    if filters.get("has_phone") is False:
        clauses.append("(phone IS NULL OR phone = '')")

    med_ids = filters.get("med_ids")
    if med_ids:
        ids = [int(m) for m in med_ids if str(m).lstrip("-").isdigit()]
        if ids:
            clauses.append(f"med_id = ANY({ph(ids)})")

    tg_ids = filters.get("tg_ids")
    if tg_ids:
        ids = [int(t) for t in tg_ids if str(t).lstrip("-").isdigit()]
        if ids:
            clauses.append(f"telegram_id = ANY({ph(ids)})")

    where = " AND ".join(clauses) if clauses else "TRUE"
    return where, params


def _crm_nick(row) -> str:
    full_name = (row["full_name"] or "").strip()
    if full_name:
        return full_name
    username = (row["username"] or "").strip()
    if username:
        return "@" + username
    return "—"


# Исключаем заблокировавших бота (им отправка невозможна)
_CRM_NOT_BLOCKED = "telegram_id NOT IN (SELECT telegram_id FROM crm.blocked_users)"


async def crm_count_users(filters: dict) -> int:
    assert _pool is not None, "db.init() ещё не вызван"
    where, params = crm_build_where(filters)
    async with _pool.acquire() as conn:
        return await conn.fetchval(
            f"SELECT count(*) FROM public.users WHERE ({where}) AND {_CRM_NOT_BLOCKED}",
            *params,
        )


async def crm_search_med_ids(query: str, limit: int = 15) -> list[int]:
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT med_id FROM public.users "
            "WHERE med_id IS NOT NULL AND CAST(med_id AS TEXT) LIKE $1 || '%' "
            "ORDER BY med_id LIMIT $2",
            query, limit,
        )
    return [r["med_id"] for r in rows]


async def crm_list_med_ids(limit: int = 1000) -> list[int]:
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT med_id FROM public.users WHERE med_id IS NOT NULL "
            "ORDER BY med_id LIMIT $1",
            limit,
        )
    return [r["med_id"] for r in rows]


async def crm_list_users(limit: int = 1000) -> list[dict]:
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT telegram_id, username, full_name FROM public.users "
            "ORDER BY created_at DESC, telegram_id LIMIT $1",
            limit,
        )
    return [{"id": r["telegram_id"], "nick": _crm_nick(r)} for r in rows]


async def crm_search_users(query: str, limit: int = 15) -> list[dict]:
    assert _pool is not None, "db.init() ещё не вызван"
    like = "%" + query + "%"
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT telegram_id, username, full_name FROM public.users "
            "WHERE CAST(telegram_id AS TEXT) LIKE $1 || '%' "
            "   OR username ILIKE $2 OR full_name ILIKE $2 "
            "ORDER BY created_at DESC, telegram_id LIMIT $3",
            query, like, limit,
        )
    return [{"id": r["telegram_id"], "nick": _crm_nick(r)} for r in rows]


async def crm_get_user(telegram_id: int) -> dict | None:
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
    for key in ("created_at", "last_bot_action_at", "last_search_at"):
        if data.get(key) is not None:
            data[key] = data[key].isoformat()
    data["blocked"] = blocked is not None
    return data


async def crm_get_user_ids(filters: dict) -> list[int]:
    assert _pool is not None, "db.init() ещё не вызван"
    where, params = crm_build_where(filters)
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT telegram_id FROM public.users WHERE ({where}) AND {_CRM_NOT_BLOCKED}",
            *params,
        )
    return [r["telegram_id"] for r in rows]


async def crm_create_pending_logs(broadcast_id: str, user_ids: list[int]) -> None:
    assert _pool is not None, "db.init() ещё не вызван"
    rows = [(broadcast_id, uid, "pending") for uid in user_ids]
    async with _pool.acquire() as conn:
        await conn.executemany(
            "INSERT INTO crm.broadcast_log (broadcast_id, telegram_id, status) "
            "VALUES ($1, $2, $3)",
            rows,
        )


async def crm_update_log_status(broadcast_id: str, telegram_id: int, status: str) -> None:
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE crm.broadcast_log SET status = $3 "
            "WHERE broadcast_id = $1 AND telegram_id = $2",
            broadcast_id, telegram_id, status,
        )


async def crm_mark_blocked(telegram_id: int) -> None:
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO crm.blocked_users (telegram_id) VALUES ($1) "
            "ON CONFLICT (telegram_id) DO NOTHING",
            telegram_id,
        )


async def crm_broadcast_status(broadcast_id: str) -> dict:
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
