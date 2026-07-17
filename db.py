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
            INSERT INTO users (telegram_id, med_id, username)
            VALUES ($1, $2, $3)
            ON CONFLICT (telegram_id) DO UPDATE SET
                med_id     = EXCLUDED.med_id,
                username   = EXCLUDED.username,
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
            INSERT INTO users (telegram_id, username, full_name, last_bot_action_at)
            VALUES ($1, $2, $3, now())
            ON CONFLICT (telegram_id) DO UPDATE SET
                username           = EXCLUDED.username,
                full_name          = EXCLUDED.full_name,
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
    """True, если пользователь уже прошёл регистрацию."""
    assert _pool is not None, "db.init() ещё не вызван"
    async with _pool.acquire() as conn:
        row = await conn.fetchval(
            "SELECT 1 FROM users WHERE telegram_id = $1", telegram_id
        )
        return row is not None


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
