"""规则与自动化 / 情景推演 / 生命周期接口（dynamic-api.md §4、§6.3–6.5），
挂在动态 router 下（/api/ontology/v1）。

页面 key 照 nav.ts：规则页 "rules"、情景推演 "scenarios"、对象类型（行为页签）"object-types"；
读 = view，写 = manage。
写接口的只读（?ver= / ?previewAs=）由全局 ReadonlyGuardDep 拦截。
"""

import contextlib
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from sqlalchemy import select

from app.common.auth import CurrentUserDep, require_func
from app.common.context import ViewContextDep
from app.common.pagination import MAX_PAGE_SIZE, Page, PageParams
from app.core.errors import NotFound
from app.db.session import SessionDep, get_session
from app.modules.dynamic.catalog import get_catalog
from app.modules.dynamic.models import ActionRun
from app.modules.dynamic.ports import PortsDep
from app.modules.dynamic.rules_schemas import (
    LifecycleDraftIn,
    ReplayIn,
    RuleCreateIn,
    RuleDraftIn,
    RuleOpenDraftIn,
    ScenarioCreateIn,
    ScenarioPatchIn,
    ScenarioRunIn,
    ScenarioStageIn,
)
from app.modules.dynamic.rules_service import LifecycleService, RuleService, ScenarioService
from app.modules.dynamic.service import ActionService, AlertService
from app.modules.versioning.models import OntologyRelease

router = APIRouter(tags=["dynamic-rules"])

RULES_VIEW = [Depends(require_func("rules", "view"))]
RULES_MANAGE = [Depends(require_func("rules", "manage"))]
SC_VIEW = [Depends(require_func("scenarios", "view"))]
SC_MANAGE = [Depends(require_func("scenarios", "manage"))]
OT_VIEW = [Depends(require_func("object-types", "view"))]
OT_MANAGE = [Depends(require_func("object-types", "manage"))]
DictPage = Page[dict[str, Any]]


def _page(default_size: int):
    def dep(
        page: Annotated[int, Query()] = 1,
        size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = default_size,
    ) -> PageParams:
        return PageParams(page=page, size=size)

    return dep


def _rules(session: SessionDep, user: CurrentUserDep) -> RuleService:
    return RuleService(session, user)


def _scenarios(session: SessionDep, user: CurrentUserDep) -> ScenarioService:
    return ScenarioService(session, user)


def _lifecycles(session: SessionDep, user: CurrentUserDep) -> LifecycleService:
    return LifecycleService(session, user)


RuleSvc = Annotated[RuleService, Depends(_rules)]
ScSvc = Annotated[ScenarioService, Depends(_scenarios)]
LcSvc = Annotated[LifecycleService, Depends(_lifecycles)]


# ---------------- 规则（R1–R12） ----------------


@router.get("/rules", dependencies=RULES_VIEW, response_model=DictPage)
async def list_rules(
    svc: RuleSvc,
    params: Annotated[PageParams, Depends(_page(16))],
    trigger: str | None = None,
    status: str | None = None,
    kw: str | None = None,
) -> DictPage:
    """R1 左侧列表：trigger = 全部 / 定时 / 事件；status = 显示状态（已发布 / 草稿中 / 审核中 / 已停用）。"""
    return await svc.list_rules(params, trigger=trigger, status=status, kw=kw)


@router.get("/rules/meta", dependencies=RULES_VIEW)
async def rules_meta(svc: RuleSvc) -> dict[str, Any]:
    """确认人、参数取值来源、筛选下拉（CONFIRMERS / MAPPING_SOURCES）。"""
    return await svc.meta()


@router.get("/rules/{rule_id}", dependencies=RULES_VIEW)
async def get_rule(
    svc: RuleSvc, rule_id: str, view: str | None = None, cr: str | None = None
) -> dict[str, Any]:
    """R4：已发布定义 + draft（有草稿时）；view=draft 时 editable。"""
    return await svc.detail(rule_id, view=view)


@router.post("/rules", dependencies=RULES_MANAGE, status_code=201)
async def create_rule(svc: RuleSvc, body: RuleCreateIn) -> dict[str, Any]:
    """新建规则（Rules.vue「新建规则」）：未发布、状态变更中；带 cr 时同时开草稿。"""
    return await svc.create(body)


@router.post("/rules/{rule_id}/draft", dependencies=RULES_MANAGE)
async def open_rule_draft(svc: RuleSvc, rule_id: str, body: RuleOpenDraftIn) -> dict[str, Any]:
    """发起变更 / 加入已有变更单：登记规则草稿（幂等）。"""
    return await svc.open_draft(rule_id, body.cr)


@router.delete("/rules/{rule_id}/draft", dependencies=RULES_MANAGE, status_code=204)
async def discard_rule_draft(svc: RuleSvc, rule_id: str, cr: str | None = None) -> None:
    """放弃本规则草稿；从未发布过的新规则一并删除。"""
    await svc.discard_draft(rule_id, cr)


@router.put("/rules/{rule_id}/draft", dependencies=RULES_MANAGE)
async def save_rule_draft(svc: RuleSvc, rule_id: str, body: RuleDraftIn) -> dict[str, Any]:
    """R6/R7 保存到草稿（暂代 PUT /change-requests/{cr}/items，target_kind=rule）[推断]。"""
    return await svc.save_draft(rule_id, body)


@router.get("/rules/{rule_id}/hits", dependencies=RULES_VIEW, response_model=DictPage)
async def rule_hits(
    svc: RuleSvc,
    rule_id: str,
    params: Annotated[PageParams, Depends(_page(12))],
    range: Literal["7d", "30d"] = "7d",
) -> DictPage:
    return await svc.hits(rule_id, params, range_=range)


@router.get("/rule-hits/{hit_id}", dependencies=RULES_VIEW)
async def rule_hit(
    svc: RuleSvc, session: SessionDep, user: CurrentUserDep, ports: PortsDep, hit_id: str
) -> dict[str, Any]:
    """R8 命中抽屉：命中 + 告警详情（时间线）+ 关联动作（该告警的第一条动作）。"""
    r, _hit, out, alert = await svc.hit(hit_id)
    catalog = get_catalog()
    timeline: list[dict[str, Any]]
    alert_detail = linked = None
    if alert is not None:
        ad = await AlertService(session, user, catalog, ports).detail(alert)
        alert_detail = ad.model_dump(by_alias=True, mode="json")
        timeline = [
            {
                "time": x["time"],
                "text": x["text"],
                "desc": x["who"],
                "status": "warn" if x.get("abnormal") else "done",
            }
            for x in alert_detail["log"]
        ]
        run = await session.scalar(
            select(ActionRun).where(ActionRun.alert_id == alert.id).order_by(ActionRun.id)
        )
        if run is not None:
            acts = ActionService(session, user, catalog, ports)
            linked = (await acts.to_out([run]))[0].model_dump(by_alias=True, mode="json")
    else:
        second = (
            f"去重键相同，冷却期（{r.cooldown}）内不再通知"
            if out["result"] == "已去重"
            else f"已通知负责人 {r.owner}"
        )
        timeline = [
            {"time": out["at"], "text": f"规则 {r.code} v{r.version} 命中", "desc": "系统", "status": "done"},
            {"time": out["at"], "text": second, "desc": "系统", "status": "done"},
        ]
    return {**out, "alert": alert_detail, "linkedAction": linked, "timeline": timeline}


@router.post("/rules/{rule_id}:replay", dependencies=RULES_MANAGE)
async def replay_rule(svc: RuleSvc, rule_id: str, body: ReplayIn) -> dict[str, Any]:
    """R9 回放试跑：不生成告警、不暂存动作、不写命中表（samples 前端按 9 条分页）。"""
    return await svc.replay(rule_id, body)


@router.get("/rules/{rule_id}/versions", dependencies=RULES_VIEW)
async def rule_versions(svc: RuleSvc, rule_id: str) -> list[dict[str, Any]]:
    return await svc.versions(rule_id)


# ---------------- 情景推演（S1–S10） ----------------


@router.get("/scenarios", dependencies=SC_VIEW, response_model=DictPage)
async def list_scenarios(
    svc: ScSvc,
    params: Annotated[PageParams, Depends(_page(12))],
    status: str | None = None,
    kw: str | None = None,
) -> DictPage:
    return await svc.list_scenarios(params, status=status, kw=kw)


@router.get("/scenarios/meta", dependencies=SC_VIEW)
async def scenarios_meta(svc: ScSvc) -> dict[str, Any]:
    return svc.meta()


@router.get("/scenarios:compare", dependencies=SC_VIEW)
async def compare_scenarios(svc: ScSvc, ids: str) -> dict[str, Any]:
    """S8：ids=a,b,c（≤3 个已完成）。"""
    return await svc.compare([x for x in ids.split(",") if x])


@router.post("/scenarios", dependencies=SC_MANAGE, status_code=201)
async def create_scenario(svc: ScSvc, body: ScenarioCreateIn | None = None) -> dict[str, Any]:
    """S2 新建 / S3 复制为新方案（from）。"""
    return await svc.create(body.from_ if body else None)


@router.get("/scenarios/{sc_id}", dependencies=SC_VIEW)
async def get_scenario(svc: ScSvc, sc_id: str) -> dict[str, Any]:
    return await svc.detail(sc_id)


@router.patch("/scenarios/{sc_id}", dependencies=SC_MANAGE)
async def rename_scenario(svc: ScSvc, sc_id: str, body: ScenarioPatchIn) -> dict[str, Any]:
    return await svc.rename(sc_id, body.name)


async def _compute_bg(request: Request, user: Any, sc_id: str) -> None:
    """进程内后台任务：新开一个会话（与请求同一个 get_session 依赖，测试里的覆盖同样生效）。"""
    dep = request.app.dependency_overrides.get(get_session, get_session)
    agen = dep()
    session = await anext(agen)
    try:
        await ScenarioService(session, user).compute(sc_id)
    except Exception as e:
        await agen.athrow(e)
        return
    with contextlib.suppress(StopAsyncIteration):
        await anext(agen)


@router.post("/scenarios/{sc_id}:run", dependencies=SC_MANAGE, status_code=202)
async def run_scenario(
    svc: ScSvc, sc_id: str, body: ScenarioRunIn, request: Request, background: BackgroundTasks
) -> dict[str, Any]:
    """S5/S6 运行 / 重新运行：校验后置“运行中”，202 返回详情；后台计算，前端轮询 /result。"""
    out = await svc.run(sc_id, body)
    background.add_task(_compute_bg, request, svc.user, sc_id)
    return out


@router.get("/scenarios/{sc_id}/result", dependencies=SC_VIEW)
async def scenario_result(svc: ScSvc, sc_id: str, response: Response) -> dict[str, Any]:
    """运行中回 202 {status}；失败回 409 SCENARIO_FAILED；完成回结果。"""
    out = await svc.result(sc_id)
    if "status" in out:
        response.status_code = 202
    return out


@router.get("/scenarios/{sc_id}/impacts", dependencies=SC_VIEW, response_model=DictPage)
async def scenario_impacts(
    svc: ScSvc, sc_id: str, params: Annotated[PageParams, Depends(_page(6))]
) -> DictPage:
    return await svc.impacts(sc_id, params)


@router.get("/scenarios/{sc_id}/stage-plan", dependencies=SC_VIEW)
async def scenario_stage_plan(svc: ScSvc, sc_id: str) -> list[dict[str, Any]]:
    return await svc.plan(sc_id)


@router.post("/scenarios/{sc_id}:stage", dependencies=SC_MANAGE)
async def stage_scenario(
    svc: ScSvc, session: SessionDep, user: CurrentUserDep, ports: PortsDep, sc_id: str, body: ScenarioStageIn
) -> dict[str, Any]:
    """S9：勾选的建议逐条暂存为待确认动作（source.type=推演，confirmer=本人）。"""
    return await svc.stage(sc_id, body.keys, ActionService(session, user, get_catalog(), ports))


# ---------------- 生命周期（L1–L6） ----------------


async def _release(session: SessionDep, ctx: ViewContextDep) -> OntologyRelease | None:
    if not ctx.ver:
        return None
    rel = await session.scalar(select(OntologyRelease).where(OntologyRelease.ver == ctx.ver))
    if rel is None:
        raise NotFound("版本不存在", details={"ver": ctx.ver})
    return rel


@router.get("/object-types/{object_id}/lifecycle", dependencies=OT_VIEW)
async def get_lifecycle(
    svc: LcSvc,
    object_id: str,
    release: Annotated[OntologyRelease | None, Depends(_release)],
    view: str | None = None,
    cr: str | None = None,
) -> dict[str, Any]:
    """L1：状态、迁移、版本；?ver= 时为该版发布时生效的状态机；view=draft 带草稿改动。"""
    return await svc.detail(object_id, view=view, cr=cr, release=release)


@router.get("/object-types/{object_id}/lifecycle/changes", dependencies=OT_VIEW, response_model=DictPage)
async def lifecycle_changes(
    svc: LcSvc,
    object_id: str,
    params: Annotated[PageParams, Depends(_page(6))],
    release: Annotated[OntologyRelease | None, Depends(_release)],
    illegal: int = 0,
) -> DictPage:
    """L2：历史版本下为空（dynamic-api.md §3）。"""
    if release is not None:
        return Page[dict[str, Any]].of([], 0, params)
    return await svc.changes(object_id, params, illegal=bool(illegal))


@router.get("/object-types/{object_id}/lifecycle/illegal", dependencies=OT_VIEW)
async def lifecycle_illegal(
    svc: LcSvc,
    object_id: str,
    release: Annotated[OntologyRelease | None, Depends(_release)],
    since: str | None = None,
) -> list[dict[str, Any]]:
    if release is not None:
        return []
    return await svc.illegal(object_id, since)


@router.put("/object-types/{object_id}/lifecycle/draft", dependencies=OT_MANAGE)
async def save_lifecycle_draft(svc: LcSvc, object_id: str, body: LifecycleDraftIn) -> dict[str, Any]:
    """L3–L6 草稿改动（暂代 cr_item section=lc_states / lc_transitions）[推断]。"""
    return await svc.save_draft(object_id, body)


__all__ = ["router"]
