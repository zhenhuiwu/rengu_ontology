"""数据源密码加密 + 真实测试连接 / 内省库表（连测试库自己）。"""

import os

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import make_url, select

from app.modules.data.models import Datasource, DatasourceTable
from app.modules.data.secrets import decrypt_password, encrypt_password
from app.seeds.loader import run_seeds

API = "/api/data/v1"
URL = make_url(os.environ["RENGU_DATABASE_URL"])


@pytest.fixture
async def ds(users):
    await run_seeds(users, ["data"])
    await users.flush()
    return users


@pytest.fixture
def real(monkeypatch):
    monkeypatch.setenv("RENGU_DS_REAL_CONNECT", "1")


def _self_db(**over):
    return {
        "name": "本库",
        "type": "PostgreSQL",
        "host": URL.host,
        "port": URL.port,
        "database": URL.database,
        "user": URL.username,
        "password": URL.password,
        "ssl": False,
        "owner": "何静",
        "mode": "手动",
        "cron": "-",
        **over,
    }


def test_encrypt_roundtrip_and_rotation(monkeypatch):
    enc = encrypt_password("p@ss")
    assert enc.startswith("fernet$") and "p@ss" not in enc and decrypt_password(enc) == "p@ss"
    assert decrypt_password(None) is None and decrypt_password("pbkdf2_sha256$1$aa$bb") is None
    k1, k2 = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setenv("RENGU_DS_SECRET_KEY", k1)
    old = encrypt_password("x")
    assert decrypt_password(enc) is None  # 换了密钥，dev 密钥加的解不开
    monkeypatch.setenv("RENGU_DS_SECRET_KEY", f"{k2},{k1}")  # 轮换：新钥在前，旧钥仍能解
    assert decrypt_password(old) == "x"


async def test_password_stored_encrypted(client, ds):
    r = await client.post(f"{API}/datasources", json=_self_db(password="s3cret-value"))
    assert r.status_code == 201 and "s3cret" not in r.text
    row = await ds.scalar(select(Datasource).where(Datasource.code == r.json()["id"]))
    assert decrypt_password(row.password_enc) == "s3cret-value"


async def test_real_test_and_introspect(client, ds, real):
    code = (await client.post(f"{API}/datasources", json=_self_db())).json()["id"]
    t = (await client.post(f"{API}/datasources/{code}:test")).json()
    assert t["ok"] is True and t["msg"] == "连接成功", t
    d = (await client.get(f"{API}/datasources/{code}")).json()
    assert d["status"] == "正常" and d["tableCount"] > 10
    items = (await client.get(f"{API}/datasources/{code}/tables", params={"size": 200})).json()["items"]
    role = next(i for i in items if i["name"] == "sys_role")
    assert role["schema"] == "public" and role["cols"] >= 10 and role["objectType"] is None
    assert not any(i["name"] == "alembic_version" for i in items)

    # 映射保留：已映射的表留着（即使源库没有），未映射的没了就删；已映射排前面
    ds_id = await ds.scalar(select(Datasource.id).where(Datasource.code == code))
    row = await ds.scalar(
        select(DatasourceTable).where(
            DatasourceTable.datasource_id == ds_id, DatasourceTable.name == "sys_role"
        )
    )
    row.object_type = "角色"
    ghost = {
        "datasource_id": ds_id,
        "schema_name": "public",
        "comment": "",
        "row_count": 0,
        "col_count": 0,
        "meta_updated_at": row.meta_updated_at,
    }
    ds.add(DatasourceTable(seq=999, name="gone_mapped", object_type="旧对象", **ghost))
    ds.add(DatasourceTable(seq=998, name="gone_plain", **ghost))
    await ds.flush()
    r = await client.post(f"{API}/datasources/{code}/tables:refresh")
    assert r.status_code == 200 and r.json()["count"] == len(items)
    items = (await client.get(f"{API}/datasources/{code}/tables", params={"size": 200})).json()["items"]
    names = [i["name"] for i in items]
    assert names[:2] == ["gone_mapped", "sys_role"] and "gone_plain" not in names
    assert items[1]["objectType"] == "角色"


async def test_real_failures(client, ds, real):
    bad = _self_db(password="wrong-pass-xyz")
    t = (await client.post(f"{API}/datasources:test", json=bad)).json()
    assert t["ok"] is False and "wrong-pass-xyz" not in t["msg"]
    code = (await client.post(f"{API}/datasources", json=bad)).json()["id"]
    t = (await client.post(f"{API}/datasources/{code}:test")).json()
    assert t["ok"] is False
    assert (await client.get(f"{API}/datasources/{code}")).json()["status"] == "未测试"
    r = await client.post(f"{API}/datasources/{code}/tables:refresh")
    assert r.status_code == 502 and "wrong-pass-xyz" not in r.text
    # seed 行没有密码：真连时直接提示重新填写
    t = (await client.post(f"{API}/datasources/DS-001:test")).json()
    assert t["ok"] is False and "未保存密码" in t["msg"]
    # 没驱动的类型（达梦）在 dev 仍走 mock
    assert (await client.post(f"{API}/datasources/DS-015:test")).json()["ok"] is True


async def test_refresh_needs_real(client, ds):
    r = await client.post(f"{API}/datasources/DS-001/tables:refresh")
    assert r.status_code == 422 and r.json()["code"] == "REAL_CONNECT_UNAVAILABLE"
