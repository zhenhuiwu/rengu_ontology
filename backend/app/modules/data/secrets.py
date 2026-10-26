"""数据源密码加密存储（可解密，真实测试连接 / 内省要用）。

- 存法：`fernet$<token>`（cryptography.Fernet，AES-128-CBC + HMAC-SHA256）。写进 datasource.password_enc。
- 密钥：环境变量 `RENGU_DS_SECRET_KEY`（Fernet.generate_key() 生成的 urlsafe base64 32 字节）。
  dev / test 没配时用固定的开发密钥（只为本地能跑，不能用于生产）；prod 没配直接报错。
- 旧数据：上一版存的是 PBKDF2 摘要（`pbkdf2_sha256$…`），解不出来，按「没有密码」处理，重新保存一次即可。
- 换密钥：`RENGU_DS_SECRET_KEY` 可以写成逗号分隔的多把，第一把加密，全部都能解密（MultiFernet 轮换）。
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import get_settings
from app.core.errors import AppError

PREFIX = "fernet$"
_DEV_SEED = b"rengu-ontology-dev-only-datasource-key"


class SecretKeyMissing(AppError):
    status_code, code, message = 500, "DS_SECRET_KEY_MISSING", "未配置数据源密码密钥 RENGU_DS_SECRET_KEY"


def _fernet() -> MultiFernet:
    raw = os.environ.get("RENGU_DS_SECRET_KEY", "").strip()
    if raw:
        return MultiFernet([Fernet(k.strip().encode()) for k in raw.split(",") if k.strip()])
    # prod，或开了真实连接（RENGU_DS_REAL_CONNECT=1，存的是真库密码）时必须配密钥，不用仓库里的开发密钥
    if get_settings().env == "prod" or os.environ.get("RENGU_DS_REAL_CONNECT") == "1":
        raise SecretKeyMissing()
    return MultiFernet([Fernet(base64.urlsafe_b64encode(hashlib.sha256(_DEV_SEED).digest()))])


def encrypt_password(raw: str) -> str:
    return PREFIX + _fernet().encrypt(raw.encode()).decode()


def decrypt_password(stored: str | None) -> str | None:
    """解不出（空、旧摘要、换了密钥）返回 None。"""
    if not stored or not stored.startswith(PREFIX):
        return None
    try:
        return _fernet().decrypt(stored[len(PREFIX) :].encode()).decode()
    except InvalidToken:
        return None
