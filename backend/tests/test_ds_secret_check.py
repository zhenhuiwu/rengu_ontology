"""数据源密码密钥启动检查（app.main lifespan → check_secret_key）。不连任何外部库。"""

import logging

import pytest
from cryptography.fernet import Fernet

from app.modules.data.secrets import SecretKeyInvalid, SecretKeyMissing, check_secret_key, log


@pytest.fixture(autouse=True)
def _capture(caplog):
    # app 的日志配置可能不往 root 传，直接把 caplog 的 handler 挂到模块 logger 上
    log.addHandler(caplog.handler)
    yield
    log.removeHandler(caplog.handler)


def test_real_connect_without_key_fails(monkeypatch, caplog):
    monkeypatch.delenv("RENGU_DS_SECRET_KEY", raising=False)
    monkeypatch.setenv("RENGU_DS_REAL_CONNECT", "1")
    with caplog.at_level(logging.ERROR), pytest.raises(SecretKeyMissing):
        check_secret_key()
    assert "RENGU_DS_SECRET_KEY" in caplog.text


def test_prod_without_key_fails(monkeypatch):
    from app.modules.data import secrets

    monkeypatch.delenv("RENGU_DS_SECRET_KEY", raising=False)
    monkeypatch.delenv("RENGU_DS_REAL_CONNECT", raising=False)
    monkeypatch.setattr(secrets, "get_settings", lambda: type("S", (), {"env": "prod"})())
    with pytest.raises(SecretKeyMissing):
        check_secret_key()


def test_dev_without_key_only_warns(monkeypatch, caplog):
    monkeypatch.delenv("RENGU_DS_SECRET_KEY", raising=False)
    monkeypatch.delenv("RENGU_DS_REAL_CONNECT", raising=False)
    with caplog.at_level(logging.WARNING):
        check_secret_key()
    assert "开发密钥" in caplog.text


def test_invalid_key_fails_without_leaking(monkeypatch, caplog):
    monkeypatch.setenv("RENGU_DS_SECRET_KEY", "not-a-fernet-key-xyz")
    with caplog.at_level(logging.ERROR), pytest.raises(SecretKeyInvalid):
        check_secret_key()
    assert "not-a-fernet-key-xyz" not in caplog.text


def test_valid_key_ok(monkeypatch):
    monkeypatch.setenv("RENGU_DS_REAL_CONNECT", "1")
    monkeypatch.setenv("RENGU_DS_SECRET_KEY", Fernet.generate_key().decode())
    check_secret_key()
