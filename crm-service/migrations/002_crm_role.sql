-- Read-only роль для CRM-сервиса.
--
-- ВАЖНО: создавать роль ТОЛЬКО через SQL Editor (этот скрипт), НЕ через
-- консоль Neon (раздел Roles). Роли, созданные в консоли Neon, автоматически
-- становятся членами neon_superuser и получают полный доступ к БД — сделать
-- их read-only невозможно. SQL-роль такого членства не получает.
--
-- Права:
--   * ТОЛЬКО чтение public.users (никакой записи в таблицы основного бота)
--   * чтение и запись в служебную схему crm
-- Пароль замени на свой и вставь в CRM_DB_URL.

CREATE ROLE crm_reader WITH LOGIN PASSWORD 'ЗАМЕНИ_НА_ПАРОЛЬ';

-- Чтение таблицы пользователей (только SELECT)
GRANT USAGE ON SCHEMA public TO crm_reader;
GRANT SELECT ON public.users TO crm_reader;

-- Доступ к служебной схеме crm (без DELETE — приложению он не нужен)
GRANT USAGE ON SCHEMA crm TO crm_reader;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA crm TO crm_reader;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA crm TO crm_reader;

-- Права на будущие таблицы схемы crm
ALTER DEFAULT PRIVILEGES IN SCHEMA crm GRANT SELECT, INSERT, UPDATE ON TABLES TO crm_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA crm GRANT USAGE, SELECT ON SEQUENCES TO crm_reader;
