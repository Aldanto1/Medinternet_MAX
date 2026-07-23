"""Обработчики апдейтов MAX-бота.

В MAX нет aiogram/Router — диспетчеризация ручная: max_bot.py получает апдейты
через long polling и вызывает handle_update(). Здесь же собираются клавиатуры
(inline_keyboard attachments) и тексты разделов.

Соответствие с Telegram-версией:
  • InlineKeyboardButton(web_app=...)  -> кнопка type="open_app" (открывает mini app)
  • InlineKeyboardButton(callback_data) -> кнопка type="callback" (payload)
  • InlineKeyboardButton(url=...)       -> кнопка type="link"
  • message.answer_photo(logo)          -> attachment type="image" (по token загрузки)
"""
import logging
from pathlib import Path
from urllib.parse import quote

import db
import link_token
from config import webapp_url, BOT_NAME
from max_client import MaxClient

logger = logging.getLogger(__name__)

SITE_URL = "https://medinternet.ru/"
SUPPORT_URL = "https://max.ru/traderx_p2p"   # TODO: заменить на реальную поддержку в MAX
AGREEMENT_URL = "https://medinternet.ru/"
# Квадратный баннер для сообщения бота: MAX кропает фото по центру до ~квадрата,
# поэтому широкий logo.png обрезался. logo_banner.png — лого по центру с полями.
LOGO_PATH = Path(__file__).resolve().parent / "webapp" / "logo_banner.png"

# Контекст бота, проставляется из max_bot.py при старте.
_client: MaxClient | None = None
_bot_name: str = BOT_NAME
# Кэш токена загруженного логотипа: грузим один раз, дальше переиспользуем.
_logo_token: str | None = None


def set_context(client: MaxClient, bot_name: str) -> None:
    """Сохраняет клиент и имя бота для использования в обработчиках."""
    global _client, _bot_name
    _client = client
    _bot_name = bot_name or BOT_NAME


def bot_link() -> str:
    """Ссылка на бота в MAX для приглашений."""
    return f"https://max.ru/{_bot_name}" if _bot_name else "https://max.ru/"


_ABOUT = (
    f'Я — бот <a href="{SITE_URL}"><b>Мединтернет</b></a>, медицинский ИИ-поисковик '
    "для врачей и фармацевтов (совместно с Сеченовским Университетом).\n\n"
    "Отвечаю на вопросы о препаратах, болезнях и схемах лечения, "
    "ищу по МКБ-10 и АТХ, даю ответы со ссылками на источники."
)

def _partners_text() -> str:
    """Текст раздела «Поделиться с другом» (ссылка в «Мединтернет» ведёт на бота)."""
    return (
        "🤝 <b>Поделиться с другом</b>\n\n"
        f'Поделитесь ботом <a href="{bot_link()}">Мединтернет</a> со своими '
        "знакомыми и коллегами.\n\n"
        "Отправьте приглашение удобным способом:"
    )

_INSTRUCTION_TEXT = (
    "📖 <b>Как задавать вопросы поисковику</b>\n\n"
    "Чтобы получать максимально точные ответы, следуйте рекомендациям:\n\n"
    "<b>1. Будьте конкретны</b>\n"
    "Указывайте возраст, сопутствующие болезни, принимаемые лекарства.\n"
    "✗ «Что делать при высоком давлении?»\n"
    "✓ «Препараты первой линии при гипертоническом кризе у пациента 60 лет с СД 2 типа?»\n\n"
    "<b>2. Используйте терминологию</b>\n"
    "Поисковик понимает аббревиатуры и стандарты (ESC, NIH, ICD-11).\n\n"
    "<b>3. Просите источники</b>\n"
    "Уточняйте: «Какие рекомендации WHO?», «Есть ли мета-анализы?».\n\n"
    "<b>4. Разбивайте сложные запросы</b>\n"
    "Пошаговые вопросы дают более структурированные ответы.\n\n"
    "<b>5. Уточняйте контекст</b>\n"
    "«У пациента ХБП 3 стадии, как это влияет?», «Какие исключения при беременности?».\n\n"
    "<b>6. Проверяйте противоречия</b>\n"
    "Если ответ вызывает сомнения: «Это противоречит данным ABCD-2023. Объясните расхождение?»"
)

_HELP_TEXT = (
    "🙋 <b>Помощь</b>\n\n"
    "Помогите нам стать лучше и найдите ответы на свои вопросы.\n\n"
    "Оставьте отзыв, напишите в поддержку или ознакомьтесь "
    "с пользовательским соглашением."
)

_TARIFF_TEXT = (
    "💳 <b>Ваш тариф: Обычный</b>\n\n"
    "Сейчас вам доступен базовый доступ к медицинскому поисковику.\n\n"
    "<b>Тариф Плюс:</b>\n"
    "• больше запросов к поисковику;\n"
    "• приоритетная обработка вопросов;\n"
    "• ранний доступ к новым возможностям.\n\n"
    "Выберите период подписки:"
)


# ---------- Сборка кнопок MAX ----------

def _btn_callback(text: str, payload: str) -> dict:
    return {"type": "callback", "text": text, "payload": payload}


def _btn_link(text: str, url: str) -> dict:
    return {"type": "link", "text": text, "url": url}


def _miniapp_button(text: str) -> dict:
    """Кнопка открытия mini app внутри MAX.

    Официальный способ открыть зарегистрированное в кабинете мини-приложение —
    диплинк https://max.ru/<botName>?startapp. MAX перехватывает его и открывает
    приложение нативно (прямая ссылка на хостинг ушла бы в браузер). Сам URL
    приложения задаётся в кабинете бота (Чат-боты → Расширенные настройки).
    """
    return {"type": "link", "text": text, "url": f"https://max.ru/{_bot_name}?startapp"}


def _keyboard(rows: list[list[dict]]) -> dict:
    """Оборачивает ряды кнопок в attachment inline_keyboard."""
    return {"type": "inline_keyboard", "payload": {"buttons": rows}}


def _main_keyboard() -> dict:
    rows = [
        [_btn_callback("🤝 Поделиться с другом", "nav:partners")],
        [_btn_callback("📖 Как пользоваться", "nav:instruction")],
        [_btn_link("📄 Политика конфиденциальности", AGREEMENT_URL)],
    ]
    url = webapp_url()
    if url:
        rows.append([_miniapp_button("🔍 Открыть Mini App")])
    return _keyboard(rows)


def _registered_keyboard() -> dict:
    rows = []
    url = webapp_url()
    if url:
        rows.append([_miniapp_button("🔍 Открыть Mini App")])
    rows.append([_btn_callback("🏠 Главная", "nav:home")])
    return _keyboard(rows)


def _back_keyboard() -> dict:
    return _keyboard([[_btn_callback("← Вернуться", "nav:home")]])


def _partners_keyboard() -> dict:
    """Кнопки шеринга приглашения (5 штук в фиксированном порядке) + возврат."""
    link = bot_link()
    invite = "Присоединяйтесь к Мединтернету — медицинскому ИИ-поисковику для врачей и фармацевтов:"
    tg_share = f"https://t.me/share/url?url={quote(link)}&text={quote(invite)}"
    # Диплинк MAX «Отправить в MAX»: параметр только text — ссылку вшиваем в текст.
    max_share = f"https://max.ru/:share?text={quote(invite + ' ' + link)}"
    wa_share = f"https://wa.me/?text={quote(invite + ' ' + link)}"
    return _keyboard([
        # В MAX есть тип кнопки clipboard — копирует payload в буфер обмена.
        [{"type": "clipboard", "text": "🔗 Скопировать ссылку", "payload": link}],
        [_btn_link("✈️ Поделиться в Telegram", tg_share)],
        [_btn_link("🔷 Поделиться в MAX", max_share)],
        [_btn_link("💬 Поделиться в WhatsApp", wa_share)],
        [_btn_callback("← Вернуться", "nav:home")],
    ])


def _help_keyboard() -> dict:
    return _keyboard([
        [_btn_callback("✍️ Оставить отзыв", "help:feedback")],
        [_btn_link("💬 Написать в поддержку", SUPPORT_URL)],
        [_btn_link("📄 Пользовательское соглашение", AGREEMENT_URL)],
        [_btn_callback("← Вернуться", "nav:home")],
    ])


def _tariff_keyboard() -> dict:
    return _keyboard([
        [_btn_callback("⭐ Плюс на неделю", "tariff:week")],
        [_btn_callback("⭐ Плюс на месяц", "tariff:month")],
        [_btn_callback("⭐ Плюс на год", "tariff:year")],
        [_btn_callback("← Вернуться", "nav:home")],
    ])


# ---------- Главное сообщение ----------

async def _main_caption(display_name: str, user_id: int) -> str:
    greeting = f"👋 Здравствуйте, <b>{display_name}</b>!\n\n"
    if await db.user_exists(user_id):
        tail = (
            "\n\n✅ Вы зарегистрированы. Медицинский поисковик доступен через "
            "кнопку «Открыть Mini App»."
        )
    else:
        tail = "\n\nДля регистрации следуйте инструкции в мини-аппе."
    return greeting + _ABOUT + tail


async def _logo_attachment() -> list | None:
    """Возвращает attachment с логотипом (грузим один раз, дальше — по токену)."""
    global _logo_token
    if _logo_token is None and _client is not None:
        _logo_token = await _client.upload_image_token(LOGO_PATH)
    if _logo_token:
        return [{"type": "image", "payload": {"token": _logo_token}}]
    return None


def _extract_mid(resp) -> str | None:
    """Достаёт id (mid) отправленного сообщения из ответа MAX (POST /messages)."""
    if not isinstance(resp, dict):
        return None
    msg = resp.get("message") if isinstance(resp.get("message"), dict) else resp
    body = msg.get("body") if isinstance(msg, dict) else None
    if isinstance(body, dict) and body.get("mid"):
        return body["mid"]
    return resp.get("mid")


async def send_main(user_id: int, display_name: str, chat_id: int | None = None) -> None:
    """Отправляет главное сообщение: логотип + приветствие + навигация.

    В чате держим не больше одного главного сообщения: перед отправкой нового
    удаляем прежнее (его id хранится в БД) и запоминаем id только что отправленного.
    Адресуем по chat_id (из апдейта), а при вызове из веб-сервера — по user_id.
    """
    caption = await _main_caption(display_name, user_id)
    kb = _main_keyboard()
    logo = await _logo_attachment()
    attachments = [kb] + (logo or [])

    prev_mid = await db.get_main_message(user_id)
    if prev_mid:
        await _safe_delete(prev_mid)

    target = {"chat_id": chat_id} if chat_id is not None else {"user_id": user_id}
    resp = await _client.send_message(**target, text=caption, fmt="html",
                                      attachments=attachments)
    mid = _extract_mid(resp)
    if mid:
        await db.set_main_message(user_id, mid)
    else:
        await db.clear_main_message(user_id)


async def _safe_delete(message_id: str | None) -> None:
    if not message_id or _client is None:
        return
    try:
        await _client.delete_message(message_id)
    except Exception:
        pass


# ---------- Точка входа: диспетчер апдейтов ----------

async def handle_update(update: dict) -> None:
    """Разбирает один апдейт MAX и вызывает нужный обработчик."""
    utype = update.get("update_type")
    try:
        if utype == "bot_started":
            await _on_bot_started(update)
        elif utype == "message_created":
            await _on_message(update)
        elif utype == "message_callback":
            await _on_callback(update)
    except Exception as e:
        logger.exception("Ошибка обработки апдейта %s: %s", utype, e)


def _display_name(user: dict) -> str:
    name = user.get("name") or " ".join(
        filter(None, [user.get("first_name"), user.get("last_name")])
    )
    return name or "коллега"


async def _on_bot_started(update: dict) -> None:
    """Пользователь нажал СТАРТ. Возможен payload (deep-link токен регистрации)."""
    user = update.get("user") or {}
    user_id = user.get("user_id")
    chat_id = update.get("chat_id") or user_id
    payload = (update.get("payload") or "").strip()
    if user_id is None:
        return
    if payload:
        await _register_via_link(chat_id, user_id, _display_name(user), payload)
        return
    await send_main(user_id, _display_name(user), chat_id)


async def _on_message(update: dict) -> None:
    """Текстовое сообщение. Обрабатываем команды /start и /help."""
    msg = update.get("message") or {}
    sender = msg.get("sender") or {}
    recipient = msg.get("recipient") or {}
    user_id = sender.get("user_id")
    chat_id = recipient.get("chat_id") or user_id
    text = ((msg.get("body") or {}).get("text") or "").strip()
    if user_id is None:
        return

    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        token = parts[1].strip() if len(parts) > 1 else ""
        if token:
            await _register_via_link(chat_id, user_id, _display_name(sender), token)
        else:
            await send_main(user_id, _display_name(sender), chat_id)
    elif text.startswith("/help"):
        await _client.send_message(
            chat_id=chat_id, fmt="html",
            text="📋 <b>Доступные команды:</b>\n\n"
                 "/start — Начать работу с ботом\n"
                 "/help — Показать это сообщение\n",
        )


async def _register_via_link(chat_id: int, user_id: int, display_name: str, token: str) -> None:
    """Регистрация по одноразовой подписанной ссылке из личного кабинета."""
    if await db.user_exists(user_id):
        await _client.send_message(
            chat_id=chat_id, text="Вы уже зарегистрированы ✅", fmt="html",
            attachments=[_registered_keyboard()],
        )
        return

    if not link_token.verify_link_token(token) or not await db.claim_link_token(token, user_id):
        await _client.send_message(
            chat_id=chat_id, fmt="html",
            text="⚠️ Ссылка недействительна или уже использована.\n"
                 "Получите новую в личном кабинете на <b>medinternet.ru</b>.",
        )
        return

    await db.register_user(user_id, display_name, display_name)
    await _client.send_message(
        chat_id=chat_id, text="🎉 <b>Регистрация успешна</b>", fmt="html",
        attachments=[_registered_keyboard()],
    )


# ---------- Навигация по callback-кнопкам ----------

async def _ack(callback_id: str | None, notification: str | None = None) -> None:
    """Best-effort подтверждение нажатия кнопки. Ошибка не должна ломать навигацию."""
    if not callback_id:
        return
    try:
        await _client.answer_callback(callback_id, notification=notification)
    except Exception as e:
        logger.warning("answer_callback не прошёл (продолжаем навигацию): %s", e)


async def _on_callback(update: dict) -> None:
    cb = update.get("callback") or {}
    callback_id = cb.get("callback_id")
    payload = cb.get("payload") or ""
    user = cb.get("user") or {}
    user_id = user.get("user_id")
    msg = update.get("message") or {}
    recipient = msg.get("recipient") or {}
    chat_id = recipient.get("chat_id") or user_id
    prev_mid = (msg.get("body") or {}).get("mid")

    logger.info("callback: payload=%r user=%s chat=%s", payload, user_id, chat_id)

    # Заглушки, которые только показывают всплывашку и не меняют экран.
    if payload in {"tariff:week", "tariff:month", "tariff:year", "help:feedback"}:
        await _ack(callback_id, "Скоро будет доступно 🔧")
        return

    # Подтверждаем нажатие «мягко»: ошибка ack не должна ломать переход по меню.
    await _ack(callback_id)

    if payload == "nav:home":
        await _safe_delete(prev_mid)
        await send_main(user_id, _display_name(user), chat_id)
    elif payload == "nav:partners":
        await _safe_delete(prev_mid)
        await _client.send_message(chat_id=chat_id, text=_partners_text(), fmt="html",
                                   attachments=[_partners_keyboard()])
    elif payload == "nav:instruction":
        await _safe_delete(prev_mid)
        await _client.send_message(chat_id=chat_id, text=_INSTRUCTION_TEXT, fmt="html",
                                   attachments=[_back_keyboard()])
    elif payload == "nav:help":
        await _safe_delete(prev_mid)
        await _client.send_message(chat_id=chat_id, text=_HELP_TEXT, fmt="html",
                                   attachments=[_help_keyboard()])
    elif payload == "nav:tariff":
        await _safe_delete(prev_mid)
        await _client.send_message(chat_id=chat_id, text=_TARIFF_TEXT, fmt="html",
                                   attachments=[_tariff_keyboard()])
