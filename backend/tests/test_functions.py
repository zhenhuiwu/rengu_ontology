"""Real function CR publication and bounded execution, without sample-output success."""

import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser
from app.core.errors import AppError
from app.modules.admin.models import SysUser
from app.modules.functions.models import FnVersion, Function
from app.modules.functions.runtime import run
from app.modules.functions.schemas import CreateIn
from app.modules.functions.service import save
from app.modules.static.models import ChangeRequest, CRStatus
from tests.function_access_support import grant_fixture_roles

B = "/api/ontology/v1"
SOURCE = "def calculate(price: float, qty: int) -> float:\n    return price * qty"


@pytest.fixture
async def functions(users):
    await grant_fixture_roles(users, {"U010001": "R-01", "U010002": "R-01", "U010006": "R-05"})
    return users


async def create(client, **changes):
    response = await client.post(
        f"{B}/functions",
        json={"name": "calculate", "title": "实际计算", "desc": "纯计算", "code": SOURCE, **changes},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def publish(client, session, cr_id):
    # Actual HTTP submit + independent reviewer + whole-release writeback.
    cr = await session.scalar(select(ChangeRequest).where(ChangeRequest.code == cr_id))
    cr.reviewer = "陈立军"
    await session.flush()
    response = await client.post(f"{B}/change-requests/{cr_id}:submit")
    assert response.status_code == 200, response.text
    response = await client.post(f"{B}/change-requests/{cr_id}:approve", headers={"X-Demo-As": "U010002"})
    assert response.status_code == 200, response.text
    response = await client.post(f"{B}/releases", json={})
    return response


async def test_functions_cr_publish_test_edit_rollback_and_discard(functions, client):
    session = functions
    assert (await client.get(f"{B}/functions")).json()["items"] == []
    created = await create(client)
    code, cr_id = created["id"], created["crId"]
    assert await session.scalar(select(func.count()).select_from(Function)) == 0
    draft = (await client.get(f"{B}/functions/{code}")).json()
    assert draft["status"] == "草稿" and draft["pending"]["cr"] == cr_id
    assert draft["inputs"] == [
        {"name": "price", "type": "float", "desc": ""},
        {"name": "qty", "type": "int", "desc": ""},
    ]
    response = await publish(client, session, cr_id)
    assert response.status_code == 201, response.text
    assert response.json()["skipped"] == []
    current = (await client.get(f"{B}/functions/{code}")).json()
    assert current["status"] == "已发布" and current["version"] == "v1.0.0" and "pending" not in current
    assert await session.scalar(select(func.count()).select_from(FnVersion)) == 1
    result = await client.post(f"{B}/functions/{code}:test", json={"input": {"price": 3.5, "qty": 4}})
    assert result.status_code == 200 and result.json()["output"] == 14
    assert result.json()["ms"] >= 0
    # No test execution writes; registered sample output is deliberately different.
    fn = await session.scalar(select(Function).where(Function.code == code))
    fn.sample_out = {"fake": 999}
    await session.flush()
    result = await client.post(f"{B}/functions/{code}:test", json={"input": {"price": 2, "qty": 4}})
    assert result.json()["output"] == 8
    assert fn.calls_7d == 0
    update = {
        "title": "实际计算更新",
        "code": SOURCE.replace("price * qty", "price * qty + 1"),
        "lockVersion": fn.lock_version,
    }
    saved = await client.put(f"{B}/functions/{code}", json=update)
    assert saved.status_code == 200, saved.text
    assert (await client.get(f"{B}/functions/{code}")).json()["version"] == "v1.0.0"
    assert (await publish(client, session, saved.json()["crId"])).status_code == 201
    current = (await client.get(f"{B}/functions/{code}")).json()
    assert current["version"] == "v1.0.1"
    rollback = await client.post(
        f"{B}/functions/{code}:rollback", json={"toVersion": "v1.0.0", "lockVersion": current["lockVersion"]}
    )
    assert rollback.status_code == 200, rollback.text
    assert (await publish(client, session, rollback.json()["crId"])).status_code == 201
    current = (await client.get(f"{B}/functions/{code}")).json()
    assert current["version"] == "v1.0.2" and current["code"] == SOURCE
    history = (await client.get(f"{B}/functions/{code}/versions")).json()["items"]
    assert [v["status"] for v in history] == ["当前", "历史", "历史"]
    fresh = await create(client, name="discard_me", code="def discard_me():\n    return 1")
    response = await client.delete(f"{B}/change-requests/{fresh['crId']}/targets/function/{fresh['id']}")
    assert response.status_code == 204
    assert (await client.get(f"{B}/functions/{fresh['id']}")).status_code == 404


async def test_functions_readonly_permissions_validation_and_stale(functions, client):
    response = await create(client)
    code = response["id"]
    for query in ("?ver=v2026.10.1", "?previewAs=U010006"):
        # Reads intentionally use current function, never reconstruct ontology snapshots.
        assert (await client.get(f"{B}/functions/{code}{query}")).status_code == 200
        for method, url, body in (
            ("post", f"/functions/{code}:test", {"input": {}}),
            ("put", f"/functions/{code}", {"title": "修改", "code": SOURCE}),
            ("post", "/functions", {"name": "new_fn", "title": "新", "code": "def new_fn(): return 1"}),
            ("post", f"/functions/{code}:rollback", {"toVersion": "v1.0.0", "lockVersion": 1}),
        ):
            result = await getattr(client, method)(B + url + query, json=body)
            assert result.status_code == 403 and result.json()["code"] == "READONLY"
    assert (
        await client.post(
            f"{B}/functions",
            headers={"X-Demo-As": "U010006"},
            json={"name": "no", "title": "无权", "code": "def no(): return 1"},
        )
    ).status_code == 403
    assert (await client.get(f"{B}/functions/FN-%00x")).status_code == 422
    assert (await client.get(f"{B}/functions?kw=%00")).status_code == 422
    for bad_text in ("\x00", "\ud800"):
        assert (
            await client.post(
                f"{B}/functions",
                content=json.dumps({"name": "bad", "title": bad_text, "code": "def bad(): return 1"}),
                headers={"Content-Type": "application/json"},
            )
        ).status_code == 422
    saved = await client.put(
        f"{B}/functions/{code}",
        json={
            "title": "修改",
            "code": SOURCE,
            "lockVersion": 1,
            "crId": response["crId"],
            "crLockVersion": response["crLockVersion"] - 1,
        },
    )
    assert saved.status_code == 409 and saved.json()["code"] == "VERSION_CONFLICT"
    assert await functions.scalar(select(func.count()).select_from(Function)) == 0


async def test_functions_bad_cr_publication_is_atomic(functions, client):
    draft = await create(client)
    from app.modules.static.models import CrItem

    item = await functions.scalar(
        select(CrItem).where(CrItem.target_id == draft["id"], CrItem.item_key == "title")
    )
    item.after = "\n"  # bypass typed route through an otherwise valid CR JSON field
    cr = await functions.scalar(select(ChangeRequest).where(ChangeRequest.code == draft["crId"]))
    cr.status = CRStatus.approved
    await functions.flush()
    result = await client.post(f"{B}/releases", json={})
    assert result.status_code == 409 and result.json()["code"] == "WRITEBACK_FAILED"
    assert await functions.scalar(select(func.count()).select_from(Function)) == 0
    assert await functions.scalar(select(func.count()).select_from(FnVersion)) == 0
    await functions.refresh(cr)
    assert cr.status == CRStatus.approved


def test_function_runtime_real_result_and_limits():
    assert run(
        "def calc(x, y=2):\n    total = x['n'] * y\n"
        "    if total > 5:\n        return {'n': total}\n    return None",
        "calc",
        {"x": {"n": 4}},
        5,
    ) == {"n": 8}
    cases = [
        ("def calc():\n    ...", {}, "FUNCTION_NOT_IMPLEMENTED"),
        ("import os\ndef calc():\n    return 1", {}, "FUNCTION_UNSUPPORTED"),
        ("def calc():\n    return __import__('os')", {}, "FUNCTION_UNSUPPORTED"),
        ("def calc(x):\n    return x.__class__", {"x": 1}, "FUNCTION_UNSUPPORTED"),
        ("def calc():\n    while True:\n        pass", {}, "FUNCTION_UNSUPPORTED"),
        ("def calc():\n    return 2 ** 999999", {}, "FUNCTION_UNSUPPORTED"),
        ("def calc(x):\n    return x * x", {"x": 2**255}, "FUNCTION_LIMIT"),
        ("def calc():\n    return 1 / 0", {}, "FUNCTION_INPUT_INVALID"),
        ("def calc(x):\n    return x", {"x": "\x00"}, "VALIDATION_FAILED"),
    ]
    for source, inputs, code in cases:
        with pytest.raises(AppError) as exc:
            run(source, "calc", inputs, 5)
        assert exc.value.code == code
    # Reusing an input repeatedly must fail before serializing an exponential result.
    repeated = (
        "def calc(x):\n"
        + "\n".join(f"    a{i} = [a{i - 1}, a{i - 1}]" if i else "    a0 = [x, x]" for i in range(16))
        + "\n    return a15"
    )
    with pytest.raises(AppError) as exc:
        run(repeated, "calc", {"x": "s" * 32_000}, 5)
    assert exc.value.code == "FUNCTION_LIMIT"


async def test_function_draft_codes_use_transaction_lock_and_rollback(engine):
    token = uuid4().hex[:16]
    actor = CurrentUser(id=f"fn-{token}", name=f"函数测试-{token}", func={"functions": "manage"})
    async with engine.begin() as setup:
        await setup.execute(insert(SysUser).values(code=actor.id, name=actor.name))
    async with (
        engine.connect() as c1,
        engine.connect() as c2,
        AsyncSession(bind=c1, expire_on_commit=False) as first,
        AsyncSession(bind=c2, expire_on_commit=False) as second,
    ):
        pending = None
        try:
            pid = await second.scalar(text("SELECT pg_backend_pid()"))
            await second.rollback()

            def body(n):
                name = f"fn_{token}_{n}"
                return CreateIn(name=name, title=f"真实并发{n}", code=f"def {name}():\n    return {n}")

            a = await save(first, actor, body(1))
            pending = asyncio.create_task(save(second, actor, body(2)))
            async with asyncio.timeout(5):
                while not await first.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_locks "
                        "WHERE pid=:pid AND locktype='advisory' AND NOT granted)"
                    ),
                    {"pid": pid},
                ):
                    if pending.done():
                        await pending
                        pytest.fail("第二事务没有等待函数编号锁")
            await first.commit()
            b = await asyncio.wait_for(pending, 5)
            assert int(b["id"][3:]) == int(a["id"][3:]) + 1
            await second.rollback()
            # Rolled-back allocation creates neither definition nor ghost target; code can be reused.
            c = await save(second, actor, body(3))
            assert c["id"] == b["id"]
            await second.rollback()
        finally:
            if pending is not None and not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            await first.rollback()
            await second.rollback()
    async with engine.begin() as cleanup:
        await cleanup.execute(delete(ChangeRequest).where(ChangeRequest.created_by == actor.id))
        await cleanup.execute(delete(SysUser).where(SysUser.code == actor.id))
