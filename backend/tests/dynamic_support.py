"""动态模块测试公用：灌种子、按姓名切身份、端口替身。"""

from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import delete, select, text

import app.main  # noqa: F401  收集阶段就建好 app：create_app 的 load_sections 先登记真实 section，免得在 sections 替身之后才 import 撞名
from app.contracts.actions import SubmitResult, SubmitStatus
from app.modules.admin.models import SysUser
from app.modules.dynamic import ports
from tests.helpers.workspace import TEST_DB
from tests.use_ops_support import grant_ops_roles as grant_fixture_roles

API = "/api/ontology/v1"


@pytest.fixture
async def dyn(session, engine, _isolated_sections):
    from app.modules.dynamic.models import ActionRunDailyCounter
    from app.modules.versioning.sections import load_sections
    from app.seeds.loader import run_seeds
    from app.seeds.registry import SEED_MODULES

    # 规则 → action_type、生命周期 → object_type、推演 → ontology_release：灌到 dynamic 为止（含依赖）
    load_sections()  # 真实 section（含本模块 rules / lifecycles），版本快照照真实流程写
    await run_seeds(session, SEED_MODULES[: SEED_MODULES.index("dynamic") + 1])
    await grant_fixture_roles(session, {"U010001": "R-01", "U010002": "R-01", "U010004": "R-01"})
    await session.commit()  # 释放 savepoint：之后接口出错回滚也不会把种子一起回滚
    # 发号有独立提交；技术计数器单独清理，保持既有种子编号断言可重复。
    async with engine.begin() as connection:
        assert connection.engine.url.database == TEST_DB
        await connection.execute(text("SELECT set_config('rengu.ontology_id','1',true)"))
        await connection.execute(delete(ActionRunDailyCounter).where(ActionRunDailyCounter.ontology_id == 1))
    try:
        yield session
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("SELECT set_config('rengu.ontology_id','1',true)"))
            await connection.execute(delete(ActionRunDailyCounter).where(ActionRunDailyCounter.ontology_id == 1))


async def as_(session, name: str) -> dict[str, str]:
    code = await session.scalar(select(SysUser.code).where(SysUser.name == name))
    assert code, name
    return {"X-Demo-As": code}


@dataclass
class FakeSubmitter:
    result: SubmitResult
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def submit(self, action_run_id: str, idempotency_key: str) -> SubmitResult:
        self.calls.append((action_run_id, idempotency_key))
        return self.result


@dataclass
class FailingChecker:
    failed: list[dict[str, str]]

    async def check(self, action_type_code: str, context: dict[str, Any]) -> list[dict[str, str]]:
        return self.failed


@dataclass
class Fakes:
    submitter: Any
    emitter: ports.RecordingEmitter
    scheduler: ports.RecordingScheduler
    checker: Any


@pytest.fixture
async def fakes(client, dyn) -> Fakes:
    """覆盖 ports 的 get_*，每个测试独立记录（client fixture 结束时清空覆盖）。"""
    from app.main import app
    from app.modules.action.models import ActionType

    # 业务测试显式配置真实审批用户；生产目录不再读取旧JSON审批路由。
    for at in await dyn.scalars(select(ActionType)):
        if at.approval != "免审" and not at.approval.startswith("免审（"):
            at.approval = "王敏"
    await dyn.flush()

    f = Fakes(
        submitter=FakeSubmitter(SubmitResult(status=SubmitStatus.SUCCESS, receipt_id="OA-1", code="200")),
        emitter=ports.RecordingEmitter(),
        scheduler=ports.RecordingScheduler(),
        checker=ports.StubSubmissionChecker(),
    )
    app.dependency_overrides[ports.get_submitter] = lambda: f.submitter
    app.dependency_overrides[ports.get_emitter] = lambda: f.emitter
    app.dependency_overrides[ports.get_scheduler] = lambda: f.scheduler
    app.dependency_overrides[ports.get_checker] = lambda: f.checker
    return f


def err(r, status: int, code: str) -> dict[str, Any]:
    assert r.status_code == status, r.text
    body = r.json()
    assert body["code"] == code, body
    return body
