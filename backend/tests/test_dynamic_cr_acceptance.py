"""动态正式写链：真实作者/审核身份、静态CR、发布快照与生命周期放弃。"""

import pytest
from sqlalchemy import select

from app.modules.dynamic.rules_models import Lifecycle, LifecycleDraft, Rule, RuleDraft
from app.modules.versioning.service import VersioningService
from tests.dynamic_support import API, dyn  # noqa: F401

AUTHOR = {"X-Demo-As": "U010004"}
ADMIN = {"X-Demo-As": "U010001"}


async def new_cr(client, title):
    response = await client.post(
        f"{API}/change-requests", json={"title": title, "reviewer": "王敏"}, headers=AUTHOR
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_rule_and_lifecycle_real_cr_approve_publish_without_skipped(valid_lifecycle):
    client, scoped, object_id = valid_lifecycle
    cr = await new_cr(client, "动态正式联合验收")
    created = await client.post(
        f"{API}/rules", json={"cr": cr, "name": "真实创建规则", "objKind": "product"}, headers=AUTHOR
    )
    assert created.status_code == 201, created.text
    rule = created.json()
    saved = await client.put(
        f"{API}/rules/{rule['id']}/draft",
        json={"cr": cr, "autoStage": {"enabled": False, "actionTypeId": "", "confirmer": "", "mapping": []}},
        headers=AUTHOR,
    )
    assert saved.status_code == 200, saved.text
    lc = (await client.get(f"{API}/object-types/{object_id}/lifecycle", headers=AUTHOR)).json()
    edits = {
        "cr": cr,
        "addStates": [{"key": "验收终态", "name": "验收终态", "kind": "终态"}],
        "addTransitions": [
            {
                "id": "TA1",
                "from": lc["states"][0]["key"],
                "to": "验收终态",
                "trigger": "动作",
                "by": "真实配置动作",
            }
        ],
    }
    lifecycle = await client.put(
        f"{API}/object-types/{object_id}/lifecycle/draft", json=edits, headers=AUTHOR
    )
    assert lifecycle.status_code == 200, lifecycle.text
    items = (await client.get(f"{API}/change-requests/{cr}/items", headers=AUTHOR)).json()
    assert {i["section"] for i in items} >= {"config", "lc_states", "lc_transitions"}
    checked = await client.post(f"{API}/change-requests/{cr}:validate", headers=AUTHOR)
    assert checked.status_code == 200, checked.text
    checks = {row["code"]: row for row in checked.json()["checks"]}
    assert checks["V1"]["result"] == checks["V2"]["result"] == "通过"
    assert "当前授权样本2条" in checks["V2"]["note"]
    submitted = await client.post(f"{API}/change-requests/{cr}:submit", headers=AUTHOR)
    assert submitted.status_code == 200, submitted.text
    approved = await client.post(f"{API}/change-requests/{cr}:approve", headers=ADMIN)
    assert approved.status_code == 200, approved.text
    published = await client.post(f"{API}/releases", json={}, headers=ADMIN)
    assert published.status_code == 201, published.text
    assert published.json()["skipped"] == []
    row = await scoped.scalar(select(Rule).where(Rule.code == rule["id"]))
    assert row.version == 1 and row.status.value == "已发布"
    assert await scoped.scalar(select(RuleDraft.id).where(RuleDraft.rule_id == row.id)) is None
    snapshot = await VersioningService().get_snapshot(scoped, published.json()["ver"])
    assert snapshot["rules"][rule["id"]]["version"] == 1
    assert any(s["key"] == "验收终态" for s in snapshot["lifecycles"][object_id]["states"])
    assert any(t["id"] == "TA1" for t in snapshot["lifecycles"][object_id]["transitions"])
    # 实际整版回滚后再次发布，保留append-only历史并分配更高版本，不能撞回旧vN。
    rollback = await client.post(f"{API}/releases/{published.json()['ver']}:rollback", json={}, headers=ADMIN)
    assert rollback.status_code == 201, rollback.text
    discarded_cr = await new_cr(client, "回滚后放弃草稿")
    assert (
        await client.post(f"{API}/rules/{rule['id']}/draft", json={"cr": discarded_cr}, headers=AUTHOR)
    ).status_code == 200
    discarded = await client.delete(
        f"{API}/rules/{rule['id']}/draft", params={"cr": discarded_cr}, headers=AUTHOR
    )
    assert discarded.status_code == 204, discarded.text
    assert await scoped.scalar(select(Rule.id).where(Rule.code == rule["id"])) is not None
    second = await new_cr(client, "回滚后再次发布")
    reopened = await client.post(f"{API}/rules/{rule['id']}/draft", json={"cr": second}, headers=AUTHOR)
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["draft"]["version"] > 1
    saved_again = await client.put(
        f"{API}/rules/{rule['id']}/draft",
        json={
            "cr": second,
            "autoStage": {"enabled": False, "actionTypeId": "", "confirmer": "", "mapping": []},
        },
        headers=AUTHOR,
    )
    assert saved_again.status_code == 200, saved_again.text
    lc_again = await client.put(
        f"{API}/object-types/{object_id}/lifecycle/draft",
        json={"cr": second, "addStates": [{"key": "回滚后终态", "name": "回滚后终态", "kind": "终态"}]},
        headers=AUTHOR,
    )
    assert lc_again.status_code == 200, lc_again.text
    assert (await client.post(f"{API}/change-requests/{second}:submit", headers=AUTHOR)).status_code == 200
    assert (await client.post(f"{API}/change-requests/{second}:approve", headers=ADMIN)).status_code == 200
    republished = await client.post(f"{API}/releases", json={}, headers=ADMIN)
    assert republished.status_code == 201, republished.text
    next_snapshot = await VersioningService().get_snapshot(scoped, republished.json()["ver"])
    assert next_snapshot["rules"][rule["id"]]["version"] > 1
    assert next_snapshot["lifecycles"][object_id]["version"] > snapshot["lifecycles"][object_id]["version"]


async def test_lifecycle_duplicate_order_primary_key_blocks_submit_without_formal_writes(client, dyn):  # noqa: F811
    from sqlalchemy import func

    from app.modules.biz.models import BizOrder
    from app.modules.dynamic.rules_models import (
        LifecycleState,
        LifecycleTransition,
        LifecycleVersion,
        RuleVersion,
    )
    from app.modules.static.models import ChangeRequest, CRStatus, ObjectType, Property
    from app.modules.versioning.models import OntologyRelease

    # Keep the historical source facts intact and derive the expected V2 result independently.
    counts = list(await dyn.scalars(select(func.count()).select_from(BizOrder).group_by(BizOrder.code)))
    assert sum(counts) == 1286
    assert sum(count > 1 for count in counts) == 2
    assert sum(count for count in counts if count > 1) == 4
    formal_tables = (
        Lifecycle,
        LifecycleState,
        LifecycleTransition,
        LifecycleVersion,
        Rule,
        RuleVersion,
        ObjectType,
        Property,
        OntologyRelease,
    )

    async def formal_rows():
        return {
            model.__tablename__: list(
                (await dyn.execute(select(model.__table__).order_by(model.id))).mappings()
            )
            for model in formal_tables
        }

    before = await formal_rows()
    cr = await new_cr(client, "订单重复主键禁止发布")
    drafted = await client.put(
        f"{API}/object-types/order/lifecycle/draft",
        json={"cr": cr, "addStates": [{"key": "未发布终态", "name": "未发布终态", "kind": "终态"}]},
        headers=AUTHOR,
    )
    assert drafted.status_code == 200, drafted.text
    checked = await client.post(f"{API}/change-requests/{cr}:validate", headers=AUTHOR)
    assert checked.status_code == 200, checked.text
    v2 = next(row for row in checked.json()["checks"] if row["code"] == "V2")
    assert v2["result"] == "阻断"
    assert "1286条发现0条空主键、2组重复主键" in v2["note"]
    submitted = await client.post(f"{API}/change-requests/{cr}:submit", headers=AUTHOR)
    assert submitted.status_code == 409, submitted.text
    assert submitted.json()["code"] == "CR_BLOCKED"
    assert submitted.json()["details"]["blocked"] == ["V2"]
    current = await dyn.scalar(select(ChangeRequest).where(ChangeRequest.code == cr))
    assert current.status == CRStatus.draft
    assert await formal_rows() == before


async def test_static_discard_deletes_lifecycle_draft_and_does_not_resurrect(client, dyn):  # noqa: F811
    cr = await new_cr(client, "放弃生命周期")
    edits = {"cr": cr, "addStates": [{"key": "被放弃", "name": "被放弃", "kind": "终态"}]}
    saved = await client.put(f"{API}/object-types/order/lifecycle/draft", json=edits, headers=AUTHOR)
    assert saved.status_code == 200, saved.text
    lc = await dyn.scalar(select(Lifecycle).where(Lifecycle.code == "order"))
    assert await dyn.scalar(
        select(LifecycleDraft.id).where(LifecycleDraft.lifecycle_id == lc.id, LifecycleDraft.cr_code == cr)
    )
    dropped = await client.delete(f"{API}/change-requests/{cr}/targets/object/order", headers=AUTHOR)
    assert dropped.status_code == 204, dropped.text
    assert (
        await dyn.scalar(
            select(LifecycleDraft.id).where(
                LifecycleDraft.lifecycle_id == lc.id, LifecycleDraft.cr_code == cr
            )
        )
        is None
    )
    reopened = (
        await client.get(
            f"{API}/object-types/order/lifecycle", params={"view": "draft", "cr": cr}, headers=AUTHOR
        )
    ).json()
    assert reopened["draft"]["edits"]["addStates"] == []


async def test_r012_existing_fixed_value_mapping_saves_without_seed_whitelist(client, dyn):  # noqa: F811
    cr = await new_cr(client, "修订R012")
    opened = await client.post(f"{API}/rules/R-012/draft", json={"cr": cr}, headers=AUTHOR)
    assert opened.status_code == 200, opened.text
    draft = opened.json()["draft"]
    assert any(m["from"] == "固定值：仓库调拨" for m in draft["autoStage"]["mapping"])
    saved = await client.put(
        f"{API}/rules/R-012/draft", json={"cr": cr, "autoStage": draft["autoStage"]}, headers=AUTHOR
    )
    assert saved.status_code == 200, saved.text


async def test_lifecycle_invalid_columns_rejected_before_draft_write(client, dyn):  # noqa: F811
    from sqlalchemy import func

    cr = await new_cr(client, "生命周期边界")
    current = (await client.get(f"{API}/object-types/order/lifecycle", headers=AUTHOR)).json()
    first = current["states"][0]["key"]
    transition = {"id": "T-new", "from": first, "to": first, "trigger": "人工", "by": "验收"}
    before = await dyn.scalar(select(func.count()).select_from(LifecycleDraft))
    rules_before = await dyn.scalar(select(func.count()).select_from(Rule))
    unknown = await client.post(
        f"{API}/rules", json={"cr": cr, "name": "无效引用", "objKind": "not_registered"}, headers=AUTHOR
    )
    assert unknown.status_code == 422, unknown.text
    assert await dyn.scalar(select(func.count()).select_from(Rule)) == rules_before
    for extra in (
        {"addTransitions": [{**transition, "id": "t" * 17}]},
        {"addTransitions": [{**transition, "trigger": []}]},
        {"addTransitions": [{**transition, "by": "x" * 201}]},
        {"addTransitions": [{**transition, "actionId": "missing_action"}]},
        {"addTransitions": [{**transition, "eventId": "missing_event"}]},
        {"addStates": [{"key": first, "name": "编号重复", "kind": "终态"}]},
        {"addStates": [{"key": "bad", "name": "bad", "kind": []}]},
        {"addStates": [{"key": "bad", "name": "bad", "kind": "终态", "desc": []}]},
        {"addStates": [{"key": "bad", "name": "坏\x00状态", "kind": "终态"}]},
        {"editTransitions": {current["transitions"][0]["id"]: {"from": []}}},
    ):
        failed = await client.put(
            f"{API}/object-types/order/lifecycle/draft", json={"cr": cr, **extra}, headers=AUTHOR
        )
        assert failed.status_code == 422, failed.text
        assert await dyn.scalar(select(func.count()).select_from(LifecycleDraft)) == before
    import json

    invalid_jsonb = json.dumps(
        {"cr": cr, "addStates": [{"key": "nan", "name": "nan", "kind": "终态", "desc": float("nan")}]}
    )
    rejected = await client.put(
        f"{API}/object-types/order/lifecycle/draft",
        content=invalid_jsonb,
        headers={**AUTHOR, "Content-Type": "application/json"},
    )
    assert rejected.status_code == 422, rejected.text
    assert await dyn.scalar(select(func.count()).select_from(LifecycleDraft)) == before


@pytest.fixture
async def valid_lifecycle(client, dyn, conn, engine, tmp_path, monkeypatch):  # noqa: F811
    """Append private fixture rows; never repair or unlock a historical seed definition."""
    import json
    import os
    from uuid import uuid4

    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.common.ontology import RUNTIME_ROLE, set_ontology_scope
    from app.core import clock
    from app.db.base import Base
    from app.db.session import get_session
    from app.main import app
    from app.modules.admin.models import SysRole, SysRoleMember, SysUser
    from app.modules.data.models import Datasource, DsStatus, DsSyncMode, DsType
    from app.modules.dynamic.sections import ensure_machine
    from app.modules.ontology.models import OntologyMember, OntologyRoleBinding, OntologyWorkspace
    from app.modules.static.models import ConflictStrategy, ObjectType, Property
    from app.modules.static.models import Lifecycle as ObjectLifecycle

    assert engine.url.database == os.environ["RENGU_TEST_DB"]
    assert engine.url.database.startswith(("rengu_test_", "rengu_fix_"))
    # Snapshot every existing mapped row in the original scope, including shared admin records.
    originals = {
        table: list((await dyn.execute(select(table).order_by(table.c.id))).mappings())
        for table in Base.metadata.tables.values()
    }
    token = uuid4().hex[:10]
    code, api, schema = f"cr_asset_{token}", f"CrAsset{token}", f"cr_source_{token}"
    ws = OntologyWorkspace(code=f"cr-acceptance-{token}", name="动态联合验收专用空间")
    role = SysRole(
        code=f"CR-ROLE-{token}",
        name=f"验收对象只读-{token}",
        func={},
        objs=[{"api": api, "objType": code, "view": True, "export": False, "actions": []}],
        changed_at=clock.now(),
        changed_by="测试",
    )
    dyn.add_all([ws, role])
    await dyn.flush()
    admin_role = await dyn.scalar(select(SysRole.id).where(SysRole.code == "R-01"))
    dyn.add_all([OntologyRoleBinding(ontology_id=ws.id, role_id=rid) for rid in (admin_role, role.id)])
    for seq, (user_code, workspace_role) in enumerate((("U010004", "editor"), ("U010001", "owner"))):
        uid = await dyn.scalar(select(SysUser.id).where(SysUser.code == user_code))
        dyn.add_all(
            [
                OntologyMember(ontology_id=ws.id, user_id=uid, role=workspace_role),
                SysRoleMember(role_id=role.id, user_id=uid, seq=seq),
            ]
        )
    await dyn.flush()
    async with engine.begin() as owner:
        await owner.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        await owner.exec_driver_sql(
            f'CREATE TABLE "{schema}".assets (ontology_id bigint NOT NULL, '
            "asset_id text NOT NULL, status text NOT NULL, "
            "PRIMARY KEY (ontology_id,asset_id))"
        )
        await owner.execute(
            text(f'INSERT INTO "{schema}".assets VALUES (:wid,:id,:status)'),
            [
                {"wid": wid, "id": key, "status": "准备"}
                for wid, key in ((ws.id, "A01"), (ws.id, "A02"), (1, "A01"))
            ],
        )
        await owner.exec_driver_sql(f'ALTER TABLE "{schema}".assets ENABLE ROW LEVEL SECURITY')
        await owner.exec_driver_sql(f'ALTER TABLE "{schema}".assets FORCE ROW LEVEL SECURITY')
        await owner.exec_driver_sql(
            f'CREATE POLICY scope ON "{schema}".assets USING '
            "(ontology_id = NULLIF(current_setting('rengu.ontology_id',true),'')::bigint)"
        )
        await owner.exec_driver_sql(f'GRANT USAGE ON SCHEMA "{schema}" TO {RUNTIME_ROLE}')
        await owner.exec_driver_sql(f'GRANT SELECT ON "{schema}".assets TO {RUNTIME_ROLE}')
    scoped = AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    previous = app.dependency_overrides.get(get_session)
    try:
        await set_ontology_scope(scoped, ws.id)
        source_code = f"CR-DS-{token}"
        scoped.add(
            Datasource(
                code=source_code,
                name=source_code,
                type=DsType.postgresql,
                host=engine.url.host,
                port=engine.url.port,
                database=engine.url.database,
                username=engine.url.username,
                ssl=False,
                status=DsStatus.ok,
                sync_mode=DsSyncMode.manual,
                created_on=clock.today(),
            )
        )
        obj = ObjectType(
            code=code,
            api_name=api,
            display_name="验收对象",
            obj_group="测试",
            description="fixture",
            conflict_strategy=ConflictStrategy.user_edit_wins,
            lifecycle=ObjectLifecycle.active,
            pk_property="asset_id",
            datasource_name=source_code,
            source_table=f"{schema}.assets",
            properties=[
                Property(
                    api_name=key,
                    display_name=key,
                    data_type="string",
                    prop_group="测试",
                    source_column=key,
                    is_pk=key == "asset_id",
                )
                for key in ("asset_id", "status")
            ],
        )
        scoped.add_all(
            [
                obj,
                ObjectType(
                    code="product",
                    api_name="Product",
                    display_name="规则对象",
                    obj_group="测试",
                    conflict_strategy=ConflictStrategy.user_edit_wins,
                    lifecycle=ObjectLifecycle.active,
                ),
            ]
        )
        await scoped.flush()
        lc = Lifecycle(
            code=code,
            object_type_id=obj.id,
            object_name=obj.display_name,
            version=1,
            field="状态（status）",
            owner="王敏",
            updated_on=clock.now(),
        )
        scoped.add(lc)
        await scoped.flush()
        await ensure_machine(
            scoped,
            lc,
            {
                "version": 1,
                "states": [{"key": "准备", "name": "准备", "kind": "初始", "x": 0, "y": 0, "count": 2}],
                "transitions": [],
            },
            release_id=None,
            by="测试",
        )
        manifest = [
            {
                "ontologyId": ws.id,
                "objectType": code,
                "datasourceId": source_code,
                "schema": schema,
                "table": "assets",
                "columns": ["asset_id", "status"],
                "urlEnv": "RENGU_CR_ACCEPTANCE_SOURCE_URL",
            }
        ]
        file = tmp_path / "cr-source.json"
        file.write_text(json.dumps(manifest), encoding="utf-8")
        monkeypatch.setenv("RENGU_OBJECT_READ_ENABLED", "1")
        monkeypatch.setenv("RENGU_OBJECT_READ_BINDINGS", str(file))
        monkeypatch.setenv("RENGU_CR_ACCEPTANCE_SOURCE_URL", engine.url.render_as_string(hide_password=False))

        async def isolated_session():
            async with scoped.begin_nested():
                yield scoped

        app.dependency_overrides[get_session] = isolated_session
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers={"X-Ontology-Id": str(ws.id)}
        ) as isolated_client:
            # A real initial publication gives the later rollback a valid parent containing this fixture.
            cr = await new_cr(isolated_client, "验收专用基线")
            response = await isolated_client.put(
                f"{API}/change-requests/{cr}/items",
                headers=AUTHOR,
                json={
                    "target": {"kind": "object", "id": code, "name": obj.display_name},
                    "section": "def",
                    "rows": [{"key": "desc", "op": "update", "before": "fixture", "after": "ready"}],
                },
            )
            assert response.status_code == 200, response.text
            for operation, headers in (("submit", AUTHOR), ("approve", ADMIN)):
                response = await isolated_client.post(
                    f"{API}/change-requests/{cr}:{operation}", headers=headers
                )
                assert response.status_code == 200, response.text
            response = await isolated_client.post(f"{API}/releases", json={}, headers=ADMIN)
            assert response.status_code == 201 and response.json()["skipped"] == [], response.text
            baseline = await VersioningService().get_snapshot(scoped, response.json()["ver"])
            assert baseline["lifecycles"][code]["version"] == 1
            assert baseline["objectTypes"][code]["pk"] == "asset_id"
            yield isolated_client, scoped, code
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_session, None)
        else:
            app.dependency_overrides[get_session] = previous
        await scoped.close()
        async with engine.begin() as owner:
            await owner.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        for table, rows in originals.items():
            if rows:
                current = (
                    (
                        await dyn.execute(
                            select(table).where(table.c.id.in_([r["id"] for r in rows])).order_by(table.c.id)
                        )
                    )
                    .mappings()
                    .all()
                )
                assert current == rows, table.name
