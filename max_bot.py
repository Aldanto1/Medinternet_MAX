"""Точка входа MAX-бота: long polling + веб-сервер mini app в одном процессе.

Аналог bot.py из Telegram-версии, но вместо aiogram — собственный клиент
MaxClient и ручной цикл получения апдейтов (GET /updates с marker).
"""
import asyncio
import logging

import db
import handlers
from config import BOT_NAME, WEBAPP_URL, validate_config, webapp_url
from max_client import MaxClient, MaxAPIError
from server import start_webserver

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

# Описание бота — показывается в профиле бота. Лимит MAX — 200 символов.
BOT_DESCRIPTION = (
    "Мединтернет — медицинский ИИ-поисковик для врачей и фармацевтов, "
    "созданный с Сеченовским Университетом. Ответы о препаратах, болезнях и "
    "схемах лечения со ссылками на источники."
)


def _update_user_id(update: dict):
    """Достаёт user_id из любого типа апдейта — для учёта активности."""
    if "user" in update and isinstance(update["user"], dict):
        return update["user"].get("user_id")
    if "callback" in update:
        return (update["callback"].get("user") or {}).get("user_id")
    if "message" in update:
        return (update["message"].get("sender") or {}).get("user_id")
    return None


async def _touch_activity(update: dict) -> None:
    """Отмечает последнее действие пользователя в боте (аналог ActivityMiddleware)."""
    uid = _update_user_id(update)
    if uid is not None:
        try:
            await db.touch_bot_action(uid)
        except Exception:
            pass  # аналитика не должна ломать обработку апдейта


async def poll_loop(client: MaxClient) -> None:
    """Бесконечный цикл long polling: тянет апдейты и раздаёт обработчикам."""
    marker: int | None = None
    logger.info("Запуск long polling...")
    while True:
        try:
            data = await client.get_updates(marker=marker, timeout=90)
        except asyncio.CancelledError:
            raise
        except MaxAPIError as e:
            logger.warning("Ошибка получения апдейтов: %s", e)
            await asyncio.sleep(3)
            continue
        except Exception as e:
            logger.warning("Сетевая ошибка long polling: %s", e)
            await asyncio.sleep(3)
            continue

        for update in data.get("updates", []):
            await _touch_activity(update)
            await handlers.handle_update(update)

        # marker указывает, с какого события продолжать (аналог offset в Telegram).
        marker = data.get("marker", marker)


async def main() -> None:
    validate_config()

    await db.init()
    logger.info("База данных подключена")

    client = MaxClient()
    await client.start()

    # Узнаём имя бота (для ссылок-приглашений и mini app), если не задано в .env.
    bot_name = BOT_NAME
    try:
        me = await client.get_me()
        bot_name = bot_name or me.get("username") or me.get("name") or ""
        logger.info("Бот: %s (id=%s)", bot_name, me.get("user_id"))
    except Exception as e:
        logger.warning("Не удалось получить /me: %s", e)

    handlers.set_context(client, bot_name)

    # Поднимаем веб-сервер mini app (в том же процессе).
    runner = await start_webserver(client, bot_name)

    # Описание бота (не критично при ошибке).
    try:
        await client.set_my_info(description=BOT_DESCRIPTION)
        logger.info("Описание бота обновлено")
    except Exception as e:
        logger.warning("Не удалось установить описание бота: %s", e)

    if WEBAPP_URL:
        logger.info("Mini app доступен по адресу: %s", webapp_url())
    else:
        logger.warning(
            "WEBAPP_URL не задан — mini app недоступен. "
            "Укажите публичный HTTPS-адрес в переменных окружения"
        )

    logger.info("Бот запускается...")
    try:
        await poll_loop(client)
    finally:
        await runner.cleanup()
        await client.close()
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
