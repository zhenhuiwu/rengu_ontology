"""The local connector opt-in must not authorize other hosts or databases."""
from types import SimpleNamespace
from app.modules.data import connector


def test_local_connection_is_exact(monkeypatch):
    monkeypatch.setenv("RENGU_DS_LOCAL_CONNECT", "1")
    monkeypatch.delenv("RENGU_DS_REAL_CONNECT", raising=False)
    monkeypatch.setattr(connector, "get_settings", lambda: SimpleNamespace(env="dev", database_url="postgresql+asyncpg://u:p@127.0.0.1:5440/main"))
    cfg = {"host": "127.0.0.1", "port": 5440, "database": "main"}
    assert connector.permitted("PostgreSQL", cfg)
    assert not connector.permitted("PostgreSQL", {**cfg, "host": "127.0.0.2"})
    assert not connector.permitted("PostgreSQL", {**cfg, "database": "other"})
    assert not connector.permitted("MySQL", cfg)
    monkeypatch.setattr(connector, "get_settings", lambda: SimpleNamespace(env="dev", database_url="postgresql+asyncpg://u:p@localhost:5440/main"))
    assert connector.permitted("PostgreSQL", {**cfg, "host": "localhost"})
    assert not connector.permitted("PostgreSQL", cfg)
