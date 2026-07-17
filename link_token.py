"""Подписанные одноразовые токены для регистрации через deep-link.

Токен = base64url(nonce[9] + HMAC(nonce)[9]) — ~24 символа, влезает в start-параметр
Telegram-ссылки (лимит 64). Подпись подтверждает, что токен выдан нами; одноразовость
обеспечивается таблицей link_tokens в БД (claim_link_token).
"""
import base64
import hashlib
import hmac
import secrets

from config import BOT_TOKEN

_KEY = (BOT_TOKEN or "").encode()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_link_token() -> str:
    nonce = secrets.token_bytes(9)
    sig = hmac.new(_KEY, nonce, hashlib.sha256).digest()[:9]
    return _b64(nonce + sig)


def verify_link_token(token: str) -> bool:
    """Проверяет подпись токена (без учёта одноразовости — та проверяется в БД)."""
    if not token:
        return False
    try:
        raw = _unb64(token)
    except Exception:
        return False
    if len(raw) != 18:
        return False
    nonce, sig = raw[:9], raw[9:]
    expected = hmac.new(_KEY, nonce, hashlib.sha256).digest()[:9]
    return hmac.compare_digest(sig, expected)
