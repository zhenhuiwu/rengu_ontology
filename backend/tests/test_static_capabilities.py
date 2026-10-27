"""能力目录 GET /meta/capabilities：分节权限、行级提示、敏感 / 隐藏属性、执行策略、API 密钥 meta:read。

运行：pytest -p tests.use_ops_support tests/test_static_capabilities.py（真实 PostgreSQL，无外部调用）
"""

from sqlalchemy import select

from app.modules.admin.models import SysRole
from app.modules.static.models import Property
from tests.test_palantir_machine import key
from tests.test_palantir_policy import set_policy
from tests.test_use_ops_runtime import API, alert_runtime, headers, runtime  # noqa: F401

URL = f"{API}/meta/capabilities"


def by_id(items):
    return {x["id"]: x for x in items}


async def add_props(session, action):
    obj = (await session.scalars(select(Property).where(Property.api_name == "is_test"))).one().object_type
    for api, sensitive, visibility in (("phone", True, "normal"), ("inner", False, "hidden")):
        session.add(
            Property(
                object_type_id=obj.id,
                api_name=api,
                display_name=api,
                data_type="string",
                prop_group="测试",
                source_column=f"store.{api}",
                is_sensitive=sensitive,
                visibility=visibility,
                seq=9,
            )
        )
    await session.flush()


async def test_capabilities_admin_sees_all_sections(alert_runtime, client):  # noqa: F811
    session, action, _rule = alert_runtime
    await add_props(session, action)
    await set_policy(session, action, {"mode": "auto", "callers": ["人"], "limits": {"dailyCount": 3}})
    res = await client.get(URL, headers=headers())
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["omitted"] == []
    store = by_id(body["objects"])["store"]
    assert store["rowAccess"] == "all"
    assert [p["api"] for p in store["properties"]] == ["is_test"]  # 敏感、隐藏属性不出现
    actions = by_id(body["actions"])
    local, cross = actions["ops_local"], actions["ops_cross"]
    assert local["policy"] == {
        "mode": "auto",
        "callers": ["人"],
        "limits": {"dailyCount": 3},
        "overflow": "stage",
    }
    assert cross["policy"]["mode"] == "confirm"  # 没有策略 = 默认人工确认
    assert local["machine"]["execute"] == {
        "method": "POST",
        "path": f"{API}/machine/actions/ops_local:execute",
        "scope": "actions:execute",
        "idempotencyHeader": "Idempotency-Key",
    }
    assert "keyAllowed" not in local["machine"]  # 人工会话不给密钥提示
    assert local["object"] == "store" and [p["api"] for p in local["params"]] == ["enabled"]
    got = by_id(body["rules"])["OPS-RULE"]
    assert got["actionType"] == "ops_local" and got["trigger"] == "event" and got["rowAccess"] == "all"
    assert isinstance(body["metrics"], list) and isinstance(body["functions"], list)
    for m in body["metrics"]:
        assert isinstance(m["trialReady"], bool)
    assert (await client.get(URL + "?ver=2026.01", headers=headers())).status_code == 422


async def test_capabilities_sections_follow_page_permissions(runtime, client):  # noqa: F811
    session, *_ = runtime
    assert (await client.get(URL, headers=headers("OPS-LIMIT"))).status_code == 403  # 一节都没有
    role = await session.scalar(select(SysRole).where(SysRole.code == "OPS-LIMIT"))
    role.func = {"object-types": "view", "action-types": "view"}
    await session.flush()
    res = await client.get(URL, headers=headers("OPS-LIMIT"))
    assert res.status_code == 200, res.text
    body = res.json()
    assert {x["section"] for x in body["omitted"]} == {"metrics", "functions", "rules"}
    assert "metrics" not in body and "rules" not in body
    assert by_id(body["objects"])["store"]["rowAccess"] == "scoped"  # 角色行级范围：大区 = 华东
    assert by_id(body["actions"])["ops_local"]["rowAccess"] == "scoped"


async def test_capabilities_api_key_meta_read(runtime, client):  # noqa: F811
    auth = {**(await key(client, ["meta:read"])), "X-Ontology-Id": "1"}
    res = await client.get(URL, headers=auth)
    assert res.status_code == 200, res.text
    body = res.json()
    assert {x["section"] for x in body["omitted"]} == {"functions", "actions", "rules"}
    assert by_id(body["objects"])["store"]["rowAccess"] == "none"  # 没有 objects:read 的密钥看不到行
    assert (await client.get(URL + "?readableOnly=true", headers=auth)).json()["objects"] == []

    both = {
        **(await key(client, ["meta:read", "actions:execute"], actionTypes=["ops_local"])),
        "X-Ontology-Id": "1",
    }
    actions = by_id((await client.get(URL, headers=both)).json()["actions"])
    assert actions["ops_local"]["machine"]["keyAllowed"] is True
    assert actions["ops_cross"]["machine"]["keyAllowed"] is False

    only_exec = {**(await key(client, ["actions:execute"])), "X-Ontology-Id": "1"}
    res = await client.get(URL, headers=only_exec)
    assert res.status_code == 403 and "meta:read" in res.json()["details"]["required"]
