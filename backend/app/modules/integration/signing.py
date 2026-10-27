"""Outbound Webhook signing and retry policy shared by action effects, action submit and event delivery.

Signing contract (docs/backend/action-event-api.md §Webhook 签名与重试):
- `X-Signature-Timestamp`: Unix seconds (UTC) at the moment of this attempt.
- `X-Signature-256`: ``sha256=`` + hex(HMAC-SHA256(secret, f"{timestamp}." + raw body bytes)).
- The raw body is exactly the bytes sent; receivers must verify before parsing JSON.
- A secret is optional per system/route. Without one, no signature headers are sent (unchanged behaviour).
  A configured but unusable secret blocks the send; it never silently falls back to unsigned.
- Secrets live with the existing credentials (SecretStr env JSON); a value may be plaintext or
  ``fernet$…`` encrypted with RENGU_DS_SECRET_KEY (app.modules.data.secrets).
"""

import hashlib
import hmac
import json
from datetime import timedelta

from pydantic import SecretStr

from app.core import clock

SIGNATURE_HEADER = "X-Signature-256"
TIMESTAMP_HEADER = "X-Signature-Timestamp"
SIGNATURE_PREFIX = "sha256="
MIN_SECRET_BYTES = 16
MAX_RETRIES = 5
BACKOFF_BASE = timedelta(seconds=30)
BACKOFF_CAP = timedelta(minutes=15)


class SigningSecretInvalid(ValueError):
    """Configured secret cannot be used; the send must not happen unsigned."""


def resolve_secret(value) -> bytes | None:
    if value is None:
        return None
    raw = value.get_secret_value() if isinstance(value, SecretStr) else value
    if not isinstance(raw, str):
        raise SigningSecretInvalid("SIGNING_SECRET_INVALID")
    if raw.startswith("fernet$"):
        from app.modules.data.secrets import decrypt_password

        raw = decrypt_password(raw)
        if raw is None:
            raise SigningSecretInvalid("SIGNING_SECRET_UNDECRYPTABLE")
    secret = raw.encode()
    if len(secret) < MIN_SECRET_BYTES or any(c < 32 or c == 127 for c in secret):
        raise SigningSecretInvalid("SIGNING_SECRET_INVALID")
    return secret


def signature(secret: bytes, timestamp: int | str, body: bytes) -> str:
    digest = hmac.new(secret, str(timestamp).encode() + b"." + body, hashlib.sha256).hexdigest()
    return SIGNATURE_PREFIX + digest


def verify(secret: bytes, timestamp: str, body: bytes, header: str, *, tolerance_s: int = 300) -> bool:
    """Reference receiver check (also used by tests): constant-time compare plus freshness window."""
    try:
        age = abs(int(clock.now().timestamp()) - int(timestamp))
    except (TypeError, ValueError):
        return False
    return age <= tolerance_s and hmac.compare_digest(signature(secret, timestamp, body), header or "")


def encode_body(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


def signed_headers(headers: dict, body: bytes, secret: bytes | None) -> dict:
    headers = {**headers, "Content-Type": "application/json"}
    if secret is not None:
        timestamp = int(clock.now().timestamp())
        headers[TIMESTAMP_HEADER] = str(timestamp)
        headers[SIGNATURE_HEADER] = signature(secret, timestamp, body)
    return headers


def retryable_status(status: int | None) -> bool:
    return status is not None and (status == 429 or 500 <= status <= 599)


def retry_budget(value) -> int:
    try:
        return max(0, min(int(value), MAX_RETRIES))
    except (TypeError, ValueError):
        return 0


def backoff(attempt: int, retry_after: str | None = None) -> timedelta:
    """attempt = number of sends already made (1 → 30s, 2 → 60s, … capped at 15 min)."""
    delay = min(BACKOFF_BASE * (2 ** max(0, attempt - 1)), BACKOFF_CAP)
    if retry_after and retry_after.strip().isdigit():
        delay = max(delay, min(timedelta(seconds=int(retry_after.strip())), BACKOFF_CAP))
    return delay
