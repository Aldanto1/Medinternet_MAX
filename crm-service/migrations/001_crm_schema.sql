-- Схема CRM-сервиса. Служебные таблицы отдельно от таблиц основного бота.
-- Запускать один раз в том же Neon-проекте.

CREATE SCHEMA IF NOT EXISTS crm;

-- Пользователи, заблокировавшие бота (403) — им больше не шлём
CREATE TABLE IF NOT EXISTS crm.blocked_users (
    telegram_id BIGINT PRIMARY KEY,
    blocked_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Лог каждой отправки в рамках рассылки
CREATE TABLE IF NOT EXISTS crm.broadcast_log (
    id           BIGSERIAL PRIMARY KEY,
    broadcast_id TEXT       NOT NULL,
    telegram_id  BIGINT     NOT NULL,
    status       TEXT       NOT NULL,   -- sent / failed / blocked / pending
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_broadcast_log_bid ON crm.broadcast_log (broadcast_id);
CREATE INDEX IF NOT EXISTS idx_broadcast_log_status ON crm.broadcast_log (broadcast_id, status);
