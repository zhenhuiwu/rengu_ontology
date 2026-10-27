"""演示主线种子补丁（seed 阶段 demo_mainline）：只新增数据 + 主线的数据前置条件。

整套 seed 在测试外层事务里跑、结束回滚；先跑除 demo_mainline 外的全部阶段拍快照，再跑补丁，
逐表比对：既有行除演示告警 assignee 外一个值都不变，其余都是新增行。
"""

import re
from urllib.parse import urlsplit

import pytest
from sqlalchemy import select, text, update

from app.core.errors import AppError
from app.modules.versioning.sections import isolated_sections, load_sections
from app.seeds.loader import run_seeds
from app.seeds.registry import SEED_MODULES

DEMO_ALERT = "AL-20261025-0139"
TABLES = {
    "sys_role": "code",
    "sys_role_scope": "code",
    "sys_role_member": "role_id::text || '/' || user_id::text",
    "ontology_member": "ontology_id::text || '/' || user_id::text",
    "ontology_role_binding": "ontology_id::text || '/' || role_id::text",
    "ext_system": "code",
    "alert": "code",
}
VOLATILE = {"id", "created_at", "updated_at", "lock_version"}


async def _snapshot(session) -> dict[str, dict[str, dict]]:
    out = {}
    for table, key in TABLES.items():
        rows = (await session.execute(text(f"SELECT {key} AS k, to_jsonb(t) AS j FROM {table} t"))).all()
        out[table] = {r.k: {c: v for c, v in r.j.items() if c not in VOLATILE} for r in rows}
    return out


@pytest.fixture
async def seeded(session):
    with isolated_sections():
        load_sections()
        await run_seeds(session, [m for m in SEED_MODULES if m != "demo_mainline"])
        before = await _snapshot(session)
        await run_seeds(session, ["demo_mainline"])
        after = await _snapshot(session)
        await run_seeds(session, ["demo_mainline"])  # 幂等
        assert await _snapshot(session) == after
    return before, after


async def test_only_adds_rows_and_sets_demo_alert_assignee(seeded):
    before, after = seeded
    changed = {
        (table, key, col)
        for table, rows in before.items()
        for key, row in rows.items()
        for col in row
        if after[table].get(key, {}).get(col) != row[col]
    }
    assert changed == {("alert", DEMO_ALERT, "assignee")}
    assert before["alert"][DEMO_ALERT]["assignee"] is None
    assert after["alert"][DEMO_ALERT]["assignee"] == "U010011"
    added = {t: sorted(set(after[t]) - set(before[t])) for t in TABLES}
    assert added["sys_role"] == ["R-09", "R-10"]
    assert added["sys_role_scope"] == ["S-101", "S-91"]
    assert len(added["sys_role_member"]) == 2
    assert len(added["ontology_member"]) == 2
    assert len(added["ontology_role_binding"]) == 3
    assert added["ext_system"] == ["EXT-WEBHOOK"]
    assert added["alert"] == []


async def test_roles_members_and_bindings(session, seeded):
    rows = (
        await session.execute(
            text("""
            SELECT r.code, r.name, u.code AS user_code, m.role AS member_role
            FROM sys_role_member rm JOIN sys_role r ON r.id = rm.role_id JOIN sys_user u ON u.id = rm.user_id
            LEFT JOIN ontology_member m ON m.user_id = u.id AND m.ontology_id = 1
            WHERE r.code IN ('R-09', 'R-10')
            """)
        )
    ).all()
    assert {tuple(r) for r in rows} == {
        ("R-09", "供应链计划员", "U010011", "editor"),
        ("R-10", "区域供应链经理", "U010812", "editor"),
    }
    bound = set(
        await session.scalars(
            text(
                "SELECT r.code FROM ontology_role_binding b JOIN sys_role r ON r.id = b.role_id "
                "WHERE b.ontology_id = 1"
            )
        )
    )
    assert bound == {"R-06", "R-09", "R-10"}


async def test_webhook_channel_is_local_and_secret_free(session, seeded):
    from app.modules.integration.models import ExtSystem

    s = await session.scalar(select(ExtSystem).where(ExtSystem.code == "EXT-WEBHOOK"))
    assert s is not None and s.category == "Webhook" and s.status.value == "正常"
    url = urlsplit(s.endpoint)
    assert url.scheme == "https" and url.hostname == "127.0.0.1" and not url.username and not url.password
    assert s.idempotent and s.idem_header == "Idempotency-Key"
    raw = (
        await session.execute(text("SELECT to_jsonb(t)::text FROM ext_system t WHERE code='EXT-WEBHOOK'"))
    ).scalar()
    assert not re.search(r"fernet\$[A-Za-z0-9_=-]{8,}|Bearer [A-Za-z0-9._-]{8,}|sk-[A-Za-z0-9]{8,}", raw)


async def test_mainline_people_resolve(session, seeded):
    from app.common.auth import resolve_user
    from app.modules.dynamic.catalog import _load_catalog

    planner = await resolve_user(session, "U010011")
    manager = await resolve_user(session, "U010812")
    assert planner.ontology_role == manager.ontology_role == "editor"
    assert "R-09" in planner.roles and "R-10" in manager.roles
    assert planner.func["alerts"] == planner.func["action-center"] == "manage"
    assert manager.func["action-center"] == "manage"
    catalog = await _load_catalog(session, "alert", rule_code="R-012", user=planner)
    # 处理人 / 确认人：告警 assignee 和 R-012 autoStage.confirmer「供应链计划员 周晓峰」都定位到 U010011
    assert catalog.person("U010011")["id"] == "U010011"
    assert catalog.person("供应链计划员 周晓峰")["id"] == "U010011"
    assert catalog.person("区域供应链经理 姚娜")["id"] == "U010812"


async def test_replenish_approver_needs_approval_text_change(session, seeded):
    """未做：request_replenish.approval 只有角色泛称，不改这段既有文本就定位不到人。
    在本事务里临时把用户编号拼到原文后面，证明只差这一处：审批人随即可解析、通过审批人权限检查。"""
    from app.common.auth import resolve_user
    from app.modules.action.models import ActionType
    from app.modules.dynamic.action_options import launch_definition
    from app.modules.dynamic.catalog import _load_catalog

    planner = await resolve_user(session, "U010011")
    original = await session.scalar(select(ActionType.approval).where(ActionType.code == "request_replenish"))
    catalog = await _load_catalog(session, "alert", rule_code="R-012", user=planner)
    assert catalog.action_meta("request_replenish")["approvalConfigured"] is False
    with pytest.raises(AppError) as err:
        await launch_definition(session, planner, catalog, "request_replenish", identity="1011846")
    assert err.value.code == "ACTION_APPROVAL_NOT_CONFIGURED"

    await session.execute(
        update(ActionType)
        .where(ActionType.code == "request_replenish")
        .values(approval=f"{original}（U010812）")
    )
    catalog = await _load_catalog(session, "alert", rule_code="R-012", user=planner)
    meta = catalog.action_meta("request_replenish")
    assert meta["approvalConfigured"] is True and meta["approverId"] == "U010812"
    try:
        await launch_definition(session, planner, catalog, "request_replenish", identity="1011846")
    except AppError as exc:  # 之后的缺口（执行通道等）不属于审批人
        assert exc.code != "ACTION_APPROVAL_NOT_CONFIGURED", exc.message
        print("next blocker:", exc.code, exc.message)
