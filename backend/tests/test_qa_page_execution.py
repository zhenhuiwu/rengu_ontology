"""Real SQL gateway + RLS/role integration. All writes target the dedicated QA test database."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.common.ontology import set_ontology_scope
from app.core import clock
from app.core.errors import AppError
from app.db.session import qa_readonly_engine_kwargs
from app.modules.qa import page_execution as replay
from app.modules.qa.models import (
    QaPageCard,
    QaSession,
    QaTemplate,
    QaTemplateCard,
    QaTurn,
    TurnKind,
    TurnStatus,
)
from app.modules.qa.repo import PageRepo
from app.modules.versioning.repo import ReleaseRepo
from tests.function_access_support import grant_fixture_roles
from tests.qa_page_test_support import committed_qa  # noqa: F401

B = "/api/qa/v1"
CHEN = {"X-Demo-As": "U010763"}


@pytest.fixture
async def data(committed_qa, session, client, engine, monkeypatch):  # noqa: F811
    from app.modules.admin.models import SysRole, SysRoleMember, SysUser

    await set_ontology_scope(session, 1)
    admin_pk = await session.scalar(select(SysUser.id).where(SysUser.code == "U010001"))
    admin_role = await session.scalar(select(SysRole.id).where(SysRole.code == "R-01"))
    await session.execute(
        delete(SysRoleMember).where(SysRoleMember.user_id == admin_pk, SysRoleMember.role_id != admin_role)
    )
    await grant_fixture_roles(session, {"U010001": "R-01", "U010763": "R-05"})
    readonly = create_async_engine(
        engine.url,
        poolclass=NullPool,
        execution_options={"postgresql_readonly": True},
        **qa_readonly_engine_kwargs(3000),
    )

    @event.listens_for(readonly.sync_engine, "begin")
    def downgrade(connection):
        connection.exec_driver_sql("SET LOCAL ROLE rengu_ontology_scoped")

    monkeypatch.setattr(replay, "get_qa_readonly_engine", lambda: readonly)
    try:
        yield SimpleNamespace(session=session, client=client, readonly=readonly)
    finally:
        await readonly.dispose()


async def source(data, sql="SELECT store_no, name FROM store ORDER BY store_no", *, status=TurnStatus.done):
    session = data.session
    release = await ReleaseRepo(session).latest()
    original = await session.scalar(select(QaSession).where(QaSession.code == "S0001"))
    number = (
        int(await session.scalar(select(func.max(QaTurn.seq)).where(QaTurn.session_id == original.id)) or 0)
        + 1
    )
    turn = QaTurn(
        code=f"QA-SOURCE-{number}",
        session_id=original.id,
        seq=number,
        kind=TurnKind.answer,
        status=status,
        question="测试专用真实只读源",
        sql_text=sql,
        release_id=release.id,
        answered_ver=release.ver,
        asked_at=clock.now(),
        snapshot={
            "answer": {
                "card": "table",
                "table": {
                    "columns": [{"key": "old", "title": "原结果"}],
                    "items": [{"old": "NEVER_COPY_OLD_RESULT"}],
                },
            }
        },
    )
    session.add(turn)
    await session.flush()
    return turn


async def create(data, *, sql=None, name="真实QA测试页"):
    turn = await source(data, sql) if sql else await source(data)
    result = await data.client.post(f"{B}/pages", json={"name": name, "sourceSessionId": "S0001"})
    assert result.status_code == 201, result.text
    assert "NEVER_COPY_OLD_RESULT" not in result.text
    return result.json(), turn


async def test_readonly_role_and_transaction_are_real(data):
    async with data.readonly.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT current_user, current_setting('transaction_read_only') ro, rolsuper, "
                    "rolbypassrls FROM pg_roles WHERE rolname=current_user"
                )
            )
        ).one()
        assert row.current_user == "rengu_ontology_scoped" and row.ro == "on"
        assert not row.rolsuper and not row.rolbypassrls
        assert await conn.scalar(text("SELECT count(*) FROM store")) == 0  # No GUC: RLS denies data.
        with pytest.raises(DBAPIError):
            await conn.execute(text("UPDATE store SET name=name"))


async def test_create_refresh_template_and_create_again_use_actual_sql(data):
    page, _ = await create(data)
    assert page["canRefresh"] and page["cards"][0]["items"]
    assert "sql" not in str(page["cards"])
    response = await data.client.post(f"{B}/pages/{page['id']}:refresh")
    assert response.status_code == 200 and response.json()["status"] == "正常", response.text
    template = await data.client.post(f"{B}/pages/{page['id']}:template", json={"name": "安全原条件模板"})
    assert template.status_code == 201, template.text
    row = await data.session.scalar(select(QaTemplate).where(QaTemplate.code == template.json()["id"]))
    caches = list(
        await data.session.scalars(
            select(QaTemplateCard.result_cache).where(QaTemplateCard.template_id == row.id)
        )
    )
    assert caches and all(cache == {} for cache in caches)
    public = await data.client.get(f"{B}/templates/{row.code}")
    assert public.status_code == 200 and public.json()["canCreate"]
    assert all("items" not in card and "sem_plan" not in card for card in public.json()["cards"])
    created = await data.client.post(
        f"{B}/pages", json={"name": "从安全模板创建", "template": {"id": row.code}}
    )
    assert created.status_code == 201 and created.json()["cards"][0]["items"], created.text


async def test_shared_viewer_runs_own_filter_without_mutating_owner_cache(data):
    from app.modules.admin.models import SysRole, SysRoleMember, SysRoleScope, SysUser
    from app.modules.static.models import ObjectType

    role = await data.session.scalar(select(SysRole).where(SysRole.code == "R-05"))
    person = await data.session.scalar(select(SysUser.id).where(SysUser.code == "U010763"))
    obj = await data.session.scalar(select(ObjectType).where(ObjectType.code == "store"))
    await data.session.execute(delete(SysRoleMember).where(SysRoleMember.user_id == person))
    data.session.add(SysRoleMember(user_id=person, role_id=role.id, seq=0))
    await data.session.execute(delete(SysRoleScope).where(SysRoleScope.role_id == role.id))
    role.objs = [{"api": obj.api_name, "objType": obj.display_name, "view": True, "export": False}]
    data.session.add(
        SysRoleScope(
            code="QA-SCOPE-HUADONG",
            role_id=role.id,
            obj_type=obj.display_name,
            prop="区域",
            op="=",
            values=["华东一区"],
            seq=0,
        )
    )
    await data.session.flush()
    page, _ = await create(data)
    shared = await data.client.put(
        f"{B}/pages/{page['id']}/share",
        json={
            "mode": "指定成员",
            "lockVersion": page["lockVersion"],
            "members": [{"userId": "U010763", "perm": "可查看"}],
            "link": True,
            "allowExport": False,
        },
    )
    assert shared.status_code == 200, shared.text
    stored = await PageRepo(data.session).detail(page["id"])
    before = deepcopy(stored.cards[0].result_cache)
    response = await data.client.get(f"{B}/pages/{page['id']}", headers=CHEN)
    assert response.status_code == 200, response.text
    assert not response.json()["cards"][0].get("denied"), response.text
    assert response.json()["cards"][0]["items"] != before["items"]
    data.session.expire_all()
    assert (await PageRepo(data.session).detail(page["id"])).cards[0].result_cache == before
    assert (await data.client.get(f"{B}/pages/{page['id']}/export", headers=CHEN)).status_code == 403


async def test_owner_current_permission_revoked_masks_prior_cache(data):
    page, _ = await create(data)
    from app.modules.admin.models import SysRole

    role = await data.session.scalar(select(SysRole).where(SysRole.code == "R-01"))
    role.objs = []
    await data.session.flush()
    response = await data.client.get(f"{B}/pages/{page['id']}")
    assert response.status_code == 200, response.text
    assert response.json()["cards"][0].get("items", []) == []


async def test_failed_source_is_not_accepted_as_success(data):
    await source(data, status=TurnStatus.failed)
    result = await data.client.post(f"{B}/pages", json={"name": "拒绝失败来源", "sourceSessionId": "S0001"})
    assert result.status_code == 422, result.text


async def test_refresh_rejects_stale_release_preserves_cache(data):
    page, _ = await create(data)
    stored = await PageRepo(data.session).detail(page["id"])
    before = deepcopy(stored.cards[0].result_cache)
    old_time = stored.last_refresh
    stored.cards[0].sem_plan = {"execution": {**stored.cards[0].sem_plan["execution"], "releaseId": -1}}
    await data.session.flush()
    response = await data.client.post(f"{B}/pages/{page['id']}:refresh")
    assert response.status_code == 200 and response.json()["status"] == "刷新失败", response.text
    stored = await PageRepo(data.session).detail(page["id"])
    assert stored.cards[0].result_cache == before and stored.last_refresh == old_time
    assert response.json()["cards"][0]["denied"] and "items" not in response.json()["cards"][0]


async def test_refresh_failure_is_atomic_for_all_cards(data):
    page, _ = await create(data)
    stored = await PageRepo(data.session).detail(page["id"])
    first = stored.cards[0]
    before = deepcopy(first.result_cache)
    stored.cards.append(
        QaPageCard(
            card_key="BAD-SECOND",
            sort_order=2,
            title="故障卡片",
            viz=first.viz,
            sem_plan={"execution": {**first.sem_plan["execution"], "sql": "SELECT 1 / 0 FROM store LIMIT 1"}},
            result_cache={"rows": [["old", "preserved"]]},
        )
    )
    await data.session.flush()
    response = await data.client.post(f"{B}/pages/{page['id']}:refresh")
    assert response.status_code == 200 and response.json()["status"] == "刷新失败", response.text
    assert (await PageRepo(data.session).detail(page["id"])).cards[0].result_cache == before


async def test_scope_and_caliber_cannot_relabel_original_sql(data):
    page, _ = await create(data)
    result = await data.client.post(f"{B}/pages/{page['id']}:template", json={"name": "保留原条件"})
    assert result.status_code == 201, result.text
    for extra in ({"scope": "别的范围"}, {"template": {"id": result.json()["id"], "caliber": "含税"}}):
        body = {"name": "不得假筛选", "template": {"id": result.json()["id"]}, **extra}
        response = await data.client.post(f"{B}/pages", json=body)
        assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM store",
        "SELECT s.* FROM store s",
        "SELECT manager FROM store",
        "SELECT count(*) FROM store WHERE manager='hidden'",
    ],
)
async def test_unpublished_or_implicit_columns_are_rejected(data, sql):
    await source(data, sql)
    response = await data.client.post(f"{B}/pages", json={"name": "拒绝隐式列", "sourceSessionId": "S0001"})
    assert response.status_code == 422, response.text


async def test_count_star_is_a_safe_aggregate(data):
    page, _ = await create(data, sql="SELECT count(*) AS n FROM store")
    assert page["cards"][0]["items"][0]["c0"] > 0


async def test_truncation_is_marked_and_export_is_bounded(data):
    page, _ = await create(data, sql="SELECT code FROM biz_order ORDER BY code")
    assert page["cards"][0]["truncated"] and len(page["cards"][0]["items"]) == 200
    response = await data.client.get(f"{B}/pages/{page['id']}/export")
    assert response.status_code == 403  # Viewing a page does not grant export.
    await export_grant(data, "order")
    response = await data.client.get(f"{B}/pages/{page['id']}/export")
    assert response.status_code == 200 and "已截断" in response.text and "最多200行" in response.text, (
        response.text
    )


async def export_grant(data, code):
    from app.modules.admin.models import SysRole
    from app.modules.static.models import ObjectType

    obj = await data.session.scalar(select(ObjectType).where(ObjectType.code == code))
    role = await data.session.scalar(select(SysRole).where(SysRole.code == "R-01"))
    role.objs = [
        {**entry, "export": True} if entry.get("api") == obj.api_name else entry for entry in role.objs
    ]
    await data.session.flush()


async def test_export_escapes_formula_cells(data):
    page, _ = await create(data, sql="SELECT '=1+1' AS formula, name FROM store LIMIT 1")
    await export_grant(data, "store")
    response = await data.client.get(f"{B}/pages/{page['id']}/export")
    assert response.status_code == 200, response.text
    assert "'=1+1" in response.text


async def test_preview_spoof_denied_and_history_never_replays(data):
    page, _ = await create(data)
    denied = await data.client.get(f"{B}/pages/{page['id']}?previewAs=U010001", headers=CHEN)
    assert denied.status_code == 403, denied.text
    old = await data.client.get(f"{B}/pages/{page['id']}?ver=v2026.10.2")
    assert old.status_code == 200 and old.json()["cards"][0]["denied"]
    assert "items" not in old.json()["cards"][0]


async def test_timeout_keeps_database_cache_and_status_accurate(data, monkeypatch):
    page, _ = await create(data)
    stored = await PageRepo(data.session).detail(page["id"])
    before = deepcopy(stored.cards[0].result_cache)
    # Exercise the real bounded connection path, not a fake execution result.
    monkeypatch.setattr(replay, "get_settings", lambda: SimpleNamespace(qa_statement_timeout_ms=0))
    response = await data.client.post(f"{B}/pages/{page['id']}:refresh")
    assert response.status_code == 200 and response.json()["status"] == "刷新失败", response.text
    assert (await PageRepo(data.session).detail(page["id"])).cards[0].result_cache == before


def test_pure_turn_plan_requires_done_status():
    release = SimpleNamespace(id=71, ver="v2026.10.1")
    turn = SimpleNamespace(
        is_seed=False,
        kind=TurnKind.answer,
        status=TurnStatus.failed,
        sql_text="SELECT count(*) FROM store",
        release_id=71,
        answered_ver="v2026.10.1",
        sem_plan=None,
    )
    with pytest.raises(AppError):
        replay.turn_plan(turn, release)


@pytest.mark.parametrize("status", [TurnStatus.streaming])
def test_pure_turn_plan_requires_completed_source(status):
    release = SimpleNamespace(id=71, ver="v2026.10.1")
    turn = SimpleNamespace(
        is_seed=False,
        kind=TurnKind.answer,
        status=status,
        sql_text="SELECT count(*) FROM store",
        release_id=71,
        answered_ver="v2026.10.1",
        sem_plan=None,
    )
    with pytest.raises(AppError):
        replay.turn_plan(turn, release)
