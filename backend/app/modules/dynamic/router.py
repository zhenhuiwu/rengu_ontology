"""告警处理 + 动作中心接口（dynamic-api.md §4），挂载在 /api/ontology/v1。

只读（?ver= / ?previewAs=）由全局 ReadonlyGuardDep 统一拦截写请求（403 READONLY）。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response, status

from app.common.auth import CurrentUserDep, require_func
from app.common.pagination import MAX_PAGE_SIZE
from app.db.session import SessionDep
from app.modules.dynamic.catalog import JsonRefCatalog, get_catalog
from app.modules.dynamic.ports import PortsDep
from app.modules.dynamic.repo import AlertRepo
from app.modules.dynamic.schemas import (
    ActionRunDetail,
    ActionRunPage,
    AlertDetail,
    AlertPage,
    BatchClaimOut,
    BatchCloseIn,
    BatchCloseOut,
    CloseIn,
    IdsIn,
    ReconcileIn,
    ResubmitOut,
    StageIn,
    StartActionOut,
    TransferIn,
    WithdrawIn,
)
from app.modules.dynamic.service import ActionService, AlertService

router = APIRouter(tags=["dynamic"])

CatalogDep = Annotated[JsonRefCatalog, Depends(get_catalog)]
PageQ = Annotated[int, Query(ge=1)]

# 功能权限（require_func 目前是桩，PDE 接入后生效）。
# 页面 key 与级别照前端 nav / roles.ts（FuncLevel = view / manage）：
# 告警处理页 key = "alerts"，动作中心 key = "action-center"；读 = view，写 = manage。
# 告警「发起动作」虽然新建动作，但操作入口在告警页，按 alerts manage 校验 [推断]。
ALERTS_VIEW = [Depends(require_func("alerts", "view"))]
ALERTS_MANAGE = [Depends(require_func("alerts", "manage"))]
ACTIONS_VIEW = [Depends(require_func("action-center", "view"))]
ACTIONS_MANAGE = [Depends(require_func("action-center", "manage"))]


def _alerts(session: SessionDep, user: CurrentUserDep, catalog: CatalogDep, ports: PortsDep) -> AlertService:
    return AlertService(session, user, catalog, ports)


def _actions(
    session: SessionDep, user: CurrentUserDep, catalog: CatalogDep, ports: PortsDep
) -> ActionService:
    return ActionService(session, user, catalog, ports)


AlertSvc = Annotated[AlertService, Depends(_alerts)]
ActionSvc = Annotated[ActionService, Depends(_actions)]


# ---------------- 告警 ----------------


@router.get("/alerts", response_model=AlertPage, dependencies=ALERTS_VIEW)
async def list_alerts(
    svc: AlertSvc,
    status_: Annotated[str, Query(alias="status")] = "open",
    level: str | None = None,
    rule_id: Annotated[str | None, Query(alias="ruleId")] = None,
    kw: str | None = None,
    page: PageQ = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 12,
) -> AlertPage:
    return await svc.search(status=status_, level=level, rule_id=rule_id, kw=kw, page=page, size=size)


@router.get("/alerts/{code}", response_model=AlertDetail, dependencies=ALERTS_VIEW)
async def get_alert(code: str, svc: AlertSvc) -> AlertDetail:
    return await svc.detail(await svc.get(code))


@router.post("/alerts/{code}:claim", response_model=AlertDetail, dependencies=ALERTS_MANAGE)
async def claim_alert(code: str, svc: AlertSvc) -> AlertDetail:
    return await svc.detail(await svc.claim(code))


@router.post("/alerts:batch-claim", response_model=BatchClaimOut, dependencies=ALERTS_MANAGE)
async def batch_claim(body: IdsIn, svc: AlertSvc) -> BatchClaimOut:
    ids = await svc.batch_claim(body.ids)
    return BatchClaimOut(claimed=len(ids), ids=ids)


@router.post("/alerts/{code}:transfer", response_model=AlertDetail, dependencies=ALERTS_MANAGE)
async def transfer_alert(code: str, body: TransferIn, svc: AlertSvc) -> AlertDetail:
    return await svc.detail(await svc.transfer(code, body.to))


@router.post("/alerts/{code}:close", response_model=AlertDetail, dependencies=ALERTS_MANAGE)
async def close_alert(code: str, body: CloseIn, svc: AlertSvc) -> AlertDetail:
    return await svc.detail(await svc.close(code, body.as_, body.reason))


@router.post("/alerts:batch-close", response_model=BatchCloseOut, dependencies=ALERTS_MANAGE)
async def batch_close(body: BatchCloseIn, svc: AlertSvc) -> BatchCloseOut:
    ids = await svc.batch_close(body.ids, body.as_, body.reason)
    return BatchCloseOut(closed=len(ids), ids=ids)


@router.post("/alerts/{code}:start-action", response_model=StartActionOut, dependencies=ALERTS_MANAGE)
async def start_action(code: str, svc: AlertSvc, acts: ActionSvc) -> StartActionOut:
    alert, run = await svc.start_action(code)
    return StartActionOut(alert=await svc.detail(alert), action=await acts.detail(run))


@router.get("/people", response_model=list[str], dependencies=ALERTS_VIEW)
async def people(session: SessionDep, _user: CurrentUserDep) -> list[str]:
    return await AlertRepo(session).people()


# ---------------- 动作 ----------------


@router.get("/action-runs", response_model=ActionRunPage, dependencies=ACTIONS_VIEW)
async def list_action_runs(
    svc: ActionSvc,
    tab: str | None = None,
    status_: Annotated[str | None, Query(alias="status")] = None,
    action: str | None = None,
    action_type: Annotated[str | None, Query(alias="actionType")] = None,
    sys_id: Annotated[str | None, Query(alias="sysId")] = None,
    obj_kind: Annotated[str | None, Query(alias="objKind")] = None,
    obj_id: Annotated[str | None, Query(alias="objId")] = None,
    alert_id: Annotated[str | None, Query(alias="alertId")] = None,
    rule_id: Annotated[str | None, Query(alias="ruleId")] = None,
    session_id: Annotated[str | None, Query(alias="sessionId")] = None,
    kw: str | None = None,
    scope: Annotated[str | None, Query(description="[推断] center/runs/all；有 tab 时固定 center")] = None,
    page: PageQ = 1,
    size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 16,
) -> ActionRunPage:
    return await svc.search(
        tab=tab,
        status=status_,
        scope=scope,
        page=page,
        size=size,
        action=action,
        action_type=action_type,
        sys_id=sys_id,
        obj_kind=obj_kind,
        obj_id=obj_id,
        alert_id=alert_id,
        rule_id=rule_id,
        session_id=session_id,
        kw=kw,
    )


@router.get("/actions/meta", dependencies=ACTIONS_VIEW)
async def actions_meta(catalog: CatalogDep, _user: CurrentUserDep) -> dict[str, Any]:
    return catalog.meta()


@router.post(
    "/action-runs:stage",
    response_model=ActionRunDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=ACTIONS_MANAGE,
)
async def stage_action(body: StageIn, svc: ActionSvc, response: Response) -> ActionRunDetail:
    run, created = await svc.stage(body)
    if not created:
        response.status_code = status.HTTP_200_OK  # 同一幂等键、内容相同：返回已有记录
    return await svc.detail(run)


@router.get("/action-runs/{code}", response_model=ActionRunDetail, dependencies=ACTIONS_VIEW)
async def get_action_run(code: str, svc: ActionSvc) -> ActionRunDetail:
    return await svc.detail(await svc.get(code))


@router.post("/action-runs/{code}:confirm", response_model=ActionRunDetail, dependencies=ACTIONS_MANAGE)
async def confirm_action(code: str, svc: ActionSvc) -> ActionRunDetail:
    return await svc.detail(await svc.confirm(code))


@router.post("/action-runs/{code}:withdraw", response_model=ActionRunDetail, dependencies=ACTIONS_MANAGE)
async def withdraw_action(code: str, body: WithdrawIn, svc: ActionSvc) -> ActionRunDetail:
    return await svc.detail(await svc.withdraw(code, body.reason))


@router.post("/action-runs/{code}:approve", response_model=ActionRunDetail, dependencies=ACTIONS_MANAGE)
async def approve_action(code: str, svc: ActionSvc) -> ActionRunDetail:
    return await svc.detail(await svc.approve(code))


@router.post("/action-runs/{code}:resubmit", response_model=ResubmitOut, dependencies=ACTIONS_MANAGE)
async def resubmit_action(code: str, svc: ActionSvc) -> ResubmitOut:
    new, old = await svc.resubmit(code)
    return ResubmitOut(
        id=new.code, idem_key=new.idem_key, action=await svc.detail(new), original=await svc.detail(old)
    )


@router.post("/action-runs/{code}:reconcile", response_model=ActionRunDetail, dependencies=ACTIONS_MANAGE)
async def reconcile_action(code: str, body: ReconcileIn, svc: ActionSvc) -> ActionRunDetail:
    return await svc.detail(await svc.reconcile(code, body.result, body.note))
