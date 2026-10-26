"""数据源密码加密 + 真实测试连接 / 内省库表（连测试库自己）。"""

import os

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import make_url, select

from app.modules.data.models import Datasource, DatasourceTable
from app.modules.data.secrets import decrypt_password, encrypt_password
from app.modules.static.models import ConflictStrategy, Lifecycle, ObjectType
from app.seeds.loader import run_seeds
from tests.function_access_support import grant_fixture_roles

API = "/api/data/v1"
URL = make_url(os.environ["RENGU_DATABASE_URL"])


@pytest.fixture
async def ds(users):
    await run_seeds(users, ["data"])
    await grant_fixture_roles(users)
    await users.flush()
    return users


@pytest.fixture
def real(monkeypatch):
    monkeypatch.setenv("RENGU_DS_REAL_CONNECT", "1")
    monkeypatch.setenv("RENGU_DS_SECRET_KEY", Fernet.generate_key().decode())  # 真连必须配密钥


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


def test_real_connect_requires_key(monkeypatch):
    monkeypatch.setenv("RENGU_DS_REAL_CONNECT", "1")
    monkeypatch.delenv("RENGU_DS_SECRET_KEY", raising=False)
    with pytest.raises(Exception, match="RENGU_DS_SECRET_KEY"):
        encrypt_password("x")


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
    for table, name in (("sys_role", "角色"), ("gone_mapped", "旧对象")):
        ds.add(
            ObjectType(
                code="map_" + table,
                api_name="Map" + table,
                display_name=name,
                obj_group="测试",
                description="",
                conflict_strategy=ConflictStrategy.user_edit_wins,
                lifecycle=Lifecycle.active,
                source_table="public." + table,
                datasource_name=code,
                owner="王敏",
                properties=[],
                seq=0,
            )
        )
    await ds.flush()
    r = await client.post(f"{API}/datasources/{code}/tables:refresh")
    assert r.status_code == 200 and r.json()["count"] == len(items)
    items = (await client.get(f"{API}/datasources/{code}/tables", params={"size": 200})).json()["items"]
    names = [i["name"] for i in items]
    assert names[:2] == ["gone_mapped", "sys_role"] and "gone_plain" not in names
    assert items[1]["objectType"] == "角色"


async def test_real_failures(client, ds, real):
    good = (await client.post(f"{API}/datasources:test", json=_self_db())).json()
    assert good["ok"] is True, good  # 新建时正确密码能连上
    bad = _self_db(password="wrong-pass-xyz")
    t = (await client.post(f"{API}/datasources:test", json=bad)).json()
    assert t["ok"] is False and "wrong-pass-xyz" not in t["msg"]
    code = (await client.post(f"{API}/datasources", json=bad)).json()["id"]
    t = (await client.post(f"{API}/datasources/{code}:test")).json()
    assert t["ok"] is False
    assert (await client.get(f"{API}/datasources/{code}")).json()["status"] == "未测试"
    r = await client.post(f"{API}/datasources/{code}/tables:refresh")
    assert r.status_code == 502 and "wrong-pass-xyz" not in r.text
    # seed 行没有密码：没有真正发起测试，也不能改写已有历史结果。
    before = (await client.get(f"{API}/datasources/DS-001")).json()
    t = (await client.post(f"{API}/datasources/DS-001:test")).json()
    assert t["ok"] is False and "未保存密码" in t["msg"]
    after = (await client.get(f"{API}/datasources/DS-001")).json()
    assert after["status"] == before["status"] and after["lastTest"] == before["lastTest"]
    # ERP 仍无数据库驱动；可选数据库驱动缺失另用无网络测试覆盖。
    r = await client.post(f"{API}/datasources:test", json=_self_db(type="ERP"))
    assert r.status_code == 422 and r.json()["code"] == "REAL_CONNECT_UNAVAILABLE"


async def test_optional_driver_missing_fails_without_connecting(monkeypatch):
    from app.modules.data import connector

    def missing(*_args, **_kwargs):
        raise ModuleNotFoundError("fixture missing driver")

    monkeypatch.setattr(connector, "_engine", missing)
    for kind in ("达梦 DM8", "MySQL", "人大金仓 KingbaseES"):
        assert connector.supports(kind)
        result = await connector.test_real(kind, {})
        assert not result.ok and "驱动未安装" in result.message
        with pytest.raises(RuntimeError, match="驱动未安装"):
            await connector.inspect_tables(kind, {}, "public", ["orders"])


async def test_legacy_mock_flag_does_not_change_real_result(client, ds, real):
    response = await client.post(f"{API}/datasources:test", json=_self_db(mockFail=True))
    assert response.status_code == 200 and response.json()["ok"] is True
    assert response.json()["msg"] == "连接成功"  # 真实独立测试库，不是旧flag控制的演示结果。


async def test_refresh_needs_real(client, ds):
    r = await client.post(f"{API}/datasources/DS-001/tables:refresh")
    assert r.status_code == 422 and r.json()["code"] == "REAL_CONNECT_UNAVAILABLE"


async def test_change_host_requires_password(client, ds):
    code = (await client.post(f"{API}/datasources", json=_self_db())).json()["id"]
    moved = _self_db(host="10.0.0.9", password=None)
    r = await client.put(f"{API}/datasources/{code}", json=moved)
    assert r.status_code == 422 and r.json()["code"] == "DS_PASSWORD_REQUIRED"
    assert (await client.put(f"{API}/datasources/{code}", json=_self_db(password=None))).status_code == 200
