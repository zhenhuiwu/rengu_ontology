"""能力目录 GET /meta/capabilities：分节权限、行级提示、敏感 / 隐藏属性、执行策略、
参数枚举、事件类型、API 密钥。

运行：pytest -p tests.use_ops_support tests/test_static_capabilities.py（真实 PostgreSQL，无外部调用）
"""

from sqlalchemy import select

from app.modules.action.models import ActionParam, ActionType, TypeStatus
from app.modules.admin.models import SysRole, SysUser
from app.modules.event.models import EventSource, EventType
from app.modules.functions.models import FnParam, Function
from app.modules.static.capabilities import literal_annotation_values
from app.modules.static.models import Property
from tests.test_palantir_machine import key
from tests.test_palantir_policy import set_policy
from tests.test_use_ops_runtime import API, alert_runtime, headers, runtime  # noqa: F401

URL = f"{API}/meta/capabilities"


def by_id(items):
    return {x["id"]: x for x in items}


async def add_props(session, action):
    # 只取外键：读 .object_type 会懒加载，身份映射里没有时在异步会话里报 MissingGreenlet（偶发失败）
    obj_id = await session.scalar(select(Property.object_type_id).where(Property.api_name == "is_test"))
    for api, sensitive, visibility in (("phone", True, "normal"), ("inner", False, "hidden")):
        session.add(
            Property(
                object_type_id=obj_id,
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


async def add_event_types(session):
    """外部来源已发布 → accepting；数据同步来源已发布 → 不接收（palantir-t4 入站只收外部来源）。"""
    for code, source in (("CapExternal", EventSource.external), ("CapSync", EventSource.sync)):
        session.add(
            EventType(
                code=code,
                api=code,
                name=code,
                source=source,
                status=TypeStatus.published,
                version="v1",
                retention_days=30,
                object_api="Store",
            )
        )
    await session.flush()


async def add_enums(session):
    """动作参数「枚举 + a / b」、函数入参 Literal[...]：两者都应带 enum；其他参数不带。"""
    cross = await session.scalar(select(ActionType).where(ActionType.code == "ops_cross"))
    session.add_all(
        [
            ActionParam(
                action_type_id=cross.id, seq=2, name="级别", api="level", type="枚举", rule_text="low / high"
            ),
            ActionParam(
                action_type_id=cross.id, seq=3, name="说明", api="note", type="枚举", rule_text="随便写"
            ),
            ActionParam(
                action_type_id=cross.id,
                seq=4,
                name="标签",
                api="tags",
                type="枚举列表",
                rule_text="x / y / z",
            ),
        ]
    )
    owner = await session.scalar(select(SysUser.id).where(SysUser.code == "U010001"))
    fn = Function(
        code="FN-CAP-ENUM",
        name="cap_enum",
        title="枚举函数",
        runtime="受限纯计算表达式",
        status="published",
        owner_id=owner,
        timeout_s=5,
        description="",
        code_text="def cap_enum(mode, n):\n    return n",
        sample_in={"secret": "不应出现"},
        sample_out=None,
    )
    session.add(fn)
    await session.flush()
    session.add_all(
        [
            FnParam(function_id=fn.id, direction="in", seq=0, name="mode", type="Literal['a', 'b']", desc=""),
            FnParam(function_id=fn.id, direction="in", seq=1, name="n", type="int", desc=""),
            FnParam(function_id=fn.id, direction="out", seq=0, name="result", type="int", desc=""),
        ]
    )
    await session.flush()
    session.expire(cross)  # 让 params 关系重新加载


def test_literal_annotation_values():
    assert literal_annotation_values("Literal['a', 'b']") == ["a", "b"]
    assert literal_annotation_values("typing.Literal[1]") == [1]
    too_many = "Literal[" + ",".join(["1"] * 33) + "]"
    for bad in ("int", "list[Literal['a']]", "Literal[x]", "Literal['a',", too_many):
        assert literal_annotation_values(bad) is None, bad


async def test_capabilities_admin_sees_all_sections(alert_runtime, client):  # noqa: F811
    session, action, _rule = alert_runtime
    await add_props(session, action)
    await add_enums(session)
    await add_event_types(session)
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
    assert local["object"] == "store" and local["params"] == [
        {"api": "enabled", "name": "测试标记", "type": "布尔", "required": True}  # 未声明取值 → 没有 enum 键
    ]
    cross_params = {p["api"]: p for p in cross["params"]}
    assert cross_params["level"]["enum"] == ["low", "high"] and cross_params["tags"]["enum"] == [
        "x",
        "y",
        "z",
    ]
    assert "enum" not in cross_params["note"] and "enum" not in cross_params["otherStore"]
    fn = by_id(body["functions"])["FN-CAP-ENUM"]
    assert [p.get("enum") for p in fn["inputs"]] == [["a", "b"], None] and "enum" not in fn["outputs"][0]
    assert "secret" not in res.text
    events = by_id(body["events"])
    done = events["ActionExecuted"]
    assert done["source"] == "动作" and done["status"] == "已发布" and done["objectApi"] == "ActionRun"
    field = {"api": "runId", "name": "执行记录", "type": "字符串", "required": True, "desc": ""}
    assert done["payload"] == [field]
    assert done["ingest"] == {
        "method": "POST",
        "path": "/internal/v1/events",
        "scope": "events:ingest",
        "eventType": "ActionExecuted",
        "accepting": False,  # 来源是动作：已发布也不收外部写入（EVENT_SOURCE_NOT_EXTERNAL）
    }
    assert events["CapExternal"]["ingest"]["accepting"] is True
    assert events["CapSync"]["ingest"]["accepting"] is False
    assert "subscribers" not in done
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
    assert {x["section"] for x in body["omitted"]} == {"metrics", "functions", "rules", "events"}
    assert "metrics" not in body and "rules" not in body and "events" not in body
    assert by_id(body["objects"])["store"]["rowAccess"] == "scoped"  # 角色行级范围：大区 = 华东
    assert by_id(body["actions"])["ops_local"]["rowAccess"] == "scoped"


async def test_capabilities_api_key_meta_read(runtime, client):  # noqa: F811
    auth = {**(await key(client, ["meta:read"])), "X-Ontology-Id": "1"}
    res = await client.get(URL, headers=auth)
    assert res.status_code == 200, res.text
    body = res.json()
    assert {x["section"] for x in body["omitted"]} == {"functions", "actions", "rules", "events"}
    assert by_id(body["objects"])["store"]["rowAccess"] == "none"  # 没有 objects:read 的密钥看不到行
    assert (await client.get(URL + "?readableOnly=true", headers=auth)).json()["objects"] == []

    both = {
        **(await key(client, ["meta:read", "actions:execute"], actionTypes=["ops_local"])),
        "X-Ontology-Id": "1",
    }
    actions = by_id((await client.get(URL, headers=both)).json()["actions"])
    assert actions["ops_local"]["machine"]["keyAllowed"] is True
    assert actions["ops_cross"]["machine"]["keyAllowed"] is False

    ingest = {
        **(await key(client, ["meta:read", "events:ingest"], eventTypes=["ActionExecuted"])),
        "X-Ontology-Id": "1",
    }
    body = (await client.get(URL, headers=ingest)).json()
    assert "events" not in {x["section"] for x in body["omitted"]}
    events = by_id(body["events"])
    assert events["ActionExecuted"]["ingest"]["keyAllowed"] is True  # 白名单命中
    assert events["ActionOutcomeUnknown"]["ingest"]["keyAllowed"] is False

    only_exec = {**(await key(client, ["actions:execute"])), "X-Ontology-Id": "1"}
    res = await client.get(URL, headers=only_exec)
    assert res.status_code == 403 and "meta:read" in res.json()["details"]["required"]
