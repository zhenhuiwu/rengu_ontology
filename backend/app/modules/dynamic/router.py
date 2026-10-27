"""告警处理 + 动作中心接口（dynamic-api.md §4），挂载在 /api/ontology/v1。

只读（?ver= / ?previewAs=）由全局 ReadonlyGuardDep 统一拦截写请求（403 READONLY）。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Path, Query, Request, Response, status

from app.common.auth import CurrentUserDep, require_func
from app.common.pagination import MAX_PAGE_SIZE, Page, PageParams
from app.db.session import SessionDep
from app.modules.admin.api_keys import require_api_scope
from app.modules.dynamic.catalog import (
    DatabaseRefCatalog,
    get_alert_catalog,
    get_catalog,
    get_list_catalog,
    get_meta_catalog,
)
from app.modules.dynamic.decisions import DecisionEvaluateIn, DecisionService
from app.modules.dynamic.ports import PortsDep
from app.modules.dynamic.schemas import (
    ActionRunDetail,
    ActionRunPage,
    AlertActionIn,
    AlertDetail,
    AlertPage,
    BatchClaimOut,
    BatchCloseIn,
    BatchCloseOut,
    CloseIn,
    IdsIn,
    LaunchValidateIn,
    MachineExecuteIn,
    ReconcileIn,
    ResubmitOut,
    StageIn,
    StartActionOut,
    TransferIn,
    WithdrawIn,
)
from app.modules.dynamic.service import ActionService, AlertService, validation_failed
from app.modules.static.explorer import parse_filters

router = APIRouter(tags=["dynamic"])

CatalogDep = Annotated[DatabaseRefCatalog, Depends(get_catalog)]
DynamicPathCode = Annotated[str, Path(pattern=r"^[^\x00]+$")]


def _page_params(default_size: int):
    """页码参数一律交给骨架 PageParams（page ≤ 0 → 1、越界收拢都由骨架做）；只有默认条数照前端。"""

    def dep(
        page: Annotated[int, Query()] = 1,
        size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = default_size,
    ) -> PageParams:
        return PageParams(page=page, size=size)

    return dep


AlertPageQ = Annotated[PageParams, Depends(_page_params(10))]
ActionPageQ = Annotated[PageParams, Depends(_page_params(10))]

# 功能权限按真实数据库角色聚合校验。
# 页面 key 与级别照前端 nav / roles.ts（FuncLevel = view / manage）：
# 告警处理页 key = "alerts"，动作中心 key = "action-center"；读 = view，写 = manage。
# 告警「发起动作」虽然新建动作，但操作入口在告警页，按 alerts manage 校验 [推断]。
ALERTS_VIEW = [Depends(require_func("alerts", "view"))]
ALERTS_MANAGE = [Depends(require_func("alerts", "manage"))]
ACTIONS_VIEW = [Depends(require_func("action-center", "view"))]
ACTIONS_MANAGE = [Depends(require_func("action-center", "manage"))]
# 只读接口另放行 API 密钥 dynamic:read（本体小白的 require_api_scope；人工会话不受影响）。
# 注意：行级数据只随 objects:read 下发，只有 dynamic:read 的密钥看到的是空列表 / 404。
DYNAMIC_READ = Depends(require_api_scope("dynamic:read"))
ALERTS_READ = [DYNAMIC_READ, *ALERTS_VIEW]
ACTIONS_READ = [DYNAMIC_READ, *ACTIONS_VIEW]


def _alerts(session: SessionDep, user: CurrentUserDep, catalog: CatalogDep, ports: PortsDep) -> AlertService:
    return AlertService(session, user, catalog, ports)


def _actions(
    session: SessionDep, user: CurrentUserDep, catalog: CatalogDep, ports: PortsDep
) -> ActionService:
    return ActionService(session, user, catalog, ports)


AlertSvc = Annotated[AlertService, Depends(_alerts)]
ActionSvc = Annotated[ActionService, Depends(_actions)]


def _alert_list(
    session: SessionDep,
    user: CurrentUserDep,
    catalog: Annotated[DatabaseRefCatalog, Depends(get_list_catalog)],
    ports: PortsDep,
) -> AlertService:
    return AlertService(session, user, catalog, ports)


def _action_list(
    session: SessionDep,
    user: CurrentUserDep,
    catalog: Annotated[DatabaseRefCatalog, Depends(get_list_catalog)],
    ports: PortsDep,
) -> ActionService:
    return ActionService(session, user, catalog, ports)


def _alert_detail(
    session: SessionDep,
    user: CurrentUserDep,
    catalog: Annotated[DatabaseRefCatalog, Depends(get_alert_catalog)],
    ports: PortsDep,
) -> AlertService:
    return AlertService(session, user, catalog, ports)


# ---------------- 告警 ----------------

FILTERS_DOC = (
    "告警对象的业务属性筛选，JSON 对象（同 GET /object-types/{id}/facets 的 filters），"
    + "须同时给且只给一个 objKind"
)


@router.get("/alerts", response_model=AlertPage, dependencies=ALERTS_READ)
async def list_alerts(
    page: AlertPageQ,
    svc: Annotated[AlertService, Depends(_alert_list)],
    status_: Annotated[str, Query(alias="status")] = "open",
    level: str | None = None,
    rule_id: Annotated[str | None, Query(alias="ruleId", description="规则编号，逗号分隔多选")] = None,
    kw: str | None = None,
    obj_kind: Annotated[str | None, Query(alias="objKind", description="对象类型，逗号分隔多选")] = None,
    dim: Annotated[list[str] | None, Query(description="维度值 objKind:id，可重复")] = None,
    filters: Annotated[str | None, Query(description=FILTERS_DOC)] = None,
) -> AlertPage:
    return await svc.search(
        status=status_,
        level=level,
        rule_id=rule_id,
        kw=kw,
        page=page,
        obj_kind=obj_kind,
        dim=dim,
        filters=parse_filters(filters),
    )


@router.get("/alerts/facets", dependencies=ALERTS_READ)
async def alert_facets(
    svc: Annotated[AlertService, Depends(_alert_list)],
    status_: Annotated[str, Query(alias="status")] = "open",
    level: str | None = None,
    rule_id: Annotated[str | None, Query(alias="ruleId")] = None,
    kw: str | None = None,
    obj_kind: Annotated[str | None, Query(alias="objKind")] = None,
    dim: Annotated[list[str] | None, Query()] = None,
    filters: Annotated[str | None, Query(description=FILTERS_DOC)] = None,
) -> dict[str, Any]:
    """告警分面（与 GET /alerts 同一组筛选参数）：每一项的计数按其余已选条件过滤。"""
    return await svc.facets(
        status=status_,
        level=level,
        rule_id=rule_id,
        kw=kw,
        obj_kind=obj_kind,
        dim=dim,
        filters=parse_filters(filters),
    )


@router.get("/alerts/meta", dependencies=ALERTS_READ)
async def alerts_meta(session: SessionDep) -> dict[str, Any]:
    from sqlalchemy import select

    from app.modules.dynamic.rules_models import Rule

    return {
        "rules": [
            {"id": code, "name": name}
            for code, name in await session.execute(select(Rule.code, Rule.name).order_by(Rule.code))
        ]
    }


@router.get("/alerts/{code}", response_model=AlertDetail, dependencies=ALERTS_READ)
async def get_alert(code: str, svc: Annotated[AlertService, Depends(_alert_detail)]) -> AlertDetail:
    # The catalog dependency validates code before its first database lookup.
    return await svc.detail(await svc.get(code))


@router.post("/alerts/{code}:claim", response_model=AlertDetail, dependencies=ALERTS_MANAGE)
async def claim_alert(code: DynamicPathCode, svc: AlertSvc) -> AlertDetail:
    return await svc.detail(await svc.claim(code))


@router.post("/alerts:batch-claim", response_model=BatchClaimOut, dependencies=ALERTS_MANAGE)
async def batch_claim(body: IdsIn, svc: AlertSvc) -> BatchClaimOut:
    ids = await svc.batch_claim(body.ids)
    return BatchClaimOut(claimed=len(ids), ids=ids)


@router.post("/alerts/{code}:transfer", response_model=AlertDetail, dependencies=ALERTS_MANAGE)
async def transfer_alert(code: DynamicPathCode, body: TransferIn, svc: AlertSvc) -> AlertDetail:
    return await svc.detail(await svc.transfer(code, body.to))


@router.post("/alerts/{code}:close", response_model=AlertDetail, dependencies=ALERTS_MANAGE)
async def close_alert(code: DynamicPathCode, body: CloseIn, svc: AlertSvc) -> AlertDetail:
    return await svc.detail(await svc.close(code, body.as_, body.reason))


@router.post("/alerts:batch-close", response_model=BatchCloseOut, dependencies=ALERTS_MANAGE)
async def batch_close(body: BatchCloseIn, svc: AlertSvc) -> BatchCloseOut:
    ids = await svc.batch_close(body.ids, body.as_, body.reason)
    return BatchCloseOut(closed=len(ids), ids=ids)


@router.post("/alerts/{code}:start-action", response_model=StartActionOut, dependencies=ALERTS_MANAGE)
async def start_action(
    code: DynamicPathCode, svc: AlertSvc, acts: ActionSvc, body: AlertActionIn
) -> StartActionOut:
    alert, run = await svc.start_action(code, body.params)
    return StartActionOut(alert=await svc.detail(alert), action=await acts.detail(run))


@router.get("/people", dependencies=ALERTS_READ)
async def people(alert_id: Annotated[str, Query(alias="alertId")], svc: AlertSvc) -> list[dict[str, str]]:
    return await svc.people(alert_id)


# ---------------- 动作 ----------------


@router.get("/action-runs", response_model=ActionRunPage, dependencies=ACTIONS_READ)
async def list_action_runs(
    page: ActionPageQ,
    svc: Annotated[ActionService, Depends(_action_list)],
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
) -> ActionRunPage:
    return await svc.search(
        tab=tab,
        status=status_,
        scope=scope,
        page=page,
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


@router.get("/actions/meta", dependencies=ACTIONS_READ)
async def actions_meta(
    catalog: Annotated[DatabaseRefCatalog, Depends(get_meta_catalog)], _user: CurrentUserDep
) -> dict[str, Any]:
    return {
        **catalog.meta(),
        "canManage": _user.func.get("action-center") == "manage"
        and _user.ontology_role in ("owner", "editor"),
    }


@router.get("/actions/available", dependencies=ACTIONS_MANAGE)
async def available_actions(session: SessionDep, user: CurrentUserDep, catalog: CatalogDep):
    from app.modules.dynamic.action_options import options

    return await options(session, user, catalog)


@router.get("/actions/{code}/targets", dependencies=ACTIONS_MANAGE)
async def action_targets(
    code: DynamicPathCode,
    session: SessionDep,
    user: CurrentUserDep,
    catalog: CatalogDep,
    page: ActionPageQ,
    kw: str | None = None,
):
    from app.modules.dynamic.action_options import targets

    return await targets(session, user, catalog, code, kw, page)


@router.post(
    "/action-runs:stage",
    response_model=ActionRunDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=ACTIONS_MANAGE,
)
async def stage_action(body: StageIn, svc: ActionSvc, response: Response) -> ActionRunDetail:
    if body.source.model_dump(exclude_none=True) != {"type": "手动"}:
        raise validation_failed("source", "请从对应来源入口发起动作；手动发起不能指定其他来源或确认人")
    run, created = await svc.stage(body)
    if not created:
        response.status_code = status.HTTP_200_OK  # 同一幂等键、内容相同：返回已有记录
    else:
        run = await svc.apply_policy(run)  # 执行策略：auto 且在上限内才自动执行，否则保持待确认
    return await svc.detail(run)


# ---- 机器接口（程序 / AI / 服务账号；API 密钥按 scope + 动作类型白名单放行，人工会话同样可用）----
async def _machine_out(run, detail: ActionRunDetail, svc: ActionService) -> dict:
    """checks：暂存时提交条件已全部通过（未通过会 422 SUBMISSION_CRITERIA_FAILED，不会到这里）；
    edits：该版定义声明的属性编辑（不是实际改动的值）；动作类型版本已变 → 两者为 null。"""
    from sqlalchemy import select

    from app.modules.action.models import ActionType

    at = await svc.session.scalar(select(ActionType).where(ActionType.code == run.action_type_code))
    same = at is not None and bool(run.type_version) and at.version == run.type_version
    return {
        "ok": True,
        "status": detail.status,
        "runId": run.code,
        "policyMode": getattr(svc, "policy_mode", None),
        "overflowReason": getattr(svc, "policy_overflow", None),
        "typeVersion": run.type_version,
        "checks": {"passed": True, "checked": len(at.conds)} if same else None,
        "edits": [{"op": e.op.value, "obj": e.obj, "field": e.field} for e in at.edits] if same else None,
        "retryable": detail.can.resubmit,
    }


@router.post(
    "/machine/actions/{type_id}:execute",
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(require_api_scope("actions:execute", action_type="{type_id}")),
        *ACTIONS_MANAGE,
    ],
    summary="按动作类型执行策略分流：auto 且在上限内自动执行，否则暂存待确认",
)
async def machine_execute(
    type_id: DynamicPathCode,
    body: MachineExecuteIn,
    svc: ActionSvc,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> dict:
    stage = StageIn.model_validate(
        {
            "typeId": type_id,
            "source": {"type": "手动"},
            "objKind": body.obj_kind,
            "objId": body.obj_id,
            "objLabel": body.obj_id,
            "params": body.params,
            "idempotencyKey": idempotency_key,
        }
    )
    run, created = await svc.stage(stage)
    if created:
        run = await svc.apply_policy(run)
    else:
        response.status_code = status.HTTP_200_OK
    return await _machine_out(run, await svc.detail(run), svc)


@router.get(
    "/machine/action-runs/{code}",
    dependencies=[Depends(require_api_scope("actions:execute")), *ACTIONS_VIEW],
    summary="机器接口：查执行结果",
)
async def machine_action_run(
    code: DynamicPathCode, svc: Annotated[ActionService, Depends(_action_list)]
) -> dict:
    run = await svc.get(code)
    return await _machine_out(run, await svc.detail(run), svc)


@router.post("/actions/{code}:validate", dependencies=ACTIONS_MANAGE)
async def validate_launch(code: DynamicPathCode, body: LaunchValidateIn, svc: ActionSvc):
    await svc.validate_launch(code, body.obj_kind, body.obj_id, body.params)
    return {"passed": True, "message": "参数和提交条件通过"}


@router.get("/action-runs/{code}", response_model=ActionRunDetail, dependencies=ACTIONS_READ)
async def get_action_run(
    code: DynamicPathCode, svc: Annotated[ActionService, Depends(_action_list)]
) -> ActionRunDetail:
    return await svc.detail(await svc.get(code))


@router.post("/action-runs/{code}:confirm", response_model=ActionRunDetail, dependencies=ACTIONS_MANAGE)
async def confirm_action(code: DynamicPathCode, svc: ActionSvc) -> ActionRunDetail:
    return await svc.detail(await svc.confirm(code))


@router.post("/action-runs/{code}:withdraw", response_model=ActionRunDetail, dependencies=ACTIONS_MANAGE)
async def withdraw_action(code: DynamicPathCode, body: WithdrawIn, svc: ActionSvc) -> ActionRunDetail:
    return await svc.detail(await svc.withdraw(code, body.reason))


@router.post("/action-runs/{code}:approve", response_model=ActionRunDetail, dependencies=ACTIONS_MANAGE)
async def approve_action(code: DynamicPathCode, svc: ActionSvc) -> ActionRunDetail:
    return await svc.detail(await svc.approve(code))


@router.post("/action-runs/{code}:resubmit", response_model=ResubmitOut, dependencies=ACTIONS_MANAGE)
async def resubmit_action(code: DynamicPathCode, svc: ActionSvc) -> ResubmitOut:
    new, old = await svc.resubmit(code)
    return ResubmitOut(
        id=new.code, idem_key=new.idem_key, action=await svc.detail(new), original=await svc.detail(old)
    )


@router.post("/action-runs/{code}:reconcile", response_model=ActionRunDetail, dependencies=ACTIONS_MANAGE)
async def reconcile_action(code: DynamicPathCode, body: ReconcileIn, svc: ActionSvc) -> ActionRunDetail:
    return await svc.detail(await svc.reconcile(code, body.result, body.note))


# ---------------- 决策评估（只给建议，不暂存不执行）----------------
# 权限：人 = alerts view（「动态页面可读」）；API 密钥另需 dynamic:read，dryRun=true 时再要 actions:dry-run
# （在 DecisionService.evaluate 里判断，dryRun=false 不需要）。调用方写进 decision_log。

DecisionPageQ = Annotated[PageParams, Depends(_page_params(20))]


def _decisions(
    request: Request,
    session: SessionDep,
    user: CurrentUserDep,
    catalog: Annotated[DatabaseRefCatalog, Depends(get_catalog)],
    ports: PortsDep,
) -> DecisionService:
    return DecisionService(session, user, catalog, ports, request)


@router.post("/decisions:evaluate", dependencies=ALERTS_READ)
async def evaluate_decision(
    body: DecisionEvaluateIn, svc: Annotated[DecisionService, Depends(_decisions)]
) -> dict[str, Any]:
    """按告警或对象给出排序后的建议动作（预填参数、试运行、执行策略），并写一条只追加的决策记录。"""
    return await svc.evaluate(body)


@router.get("/decisions", dependencies=ALERTS_READ)
async def list_decisions(
    page: DecisionPageQ,
    svc: Annotated[DecisionService, Depends(_decisions)],
    obj_kind: Annotated[str | None, Query(alias="objKind", max_length=64)] = None,
    obj_id: Annotated[str | None, Query(alias="objId", max_length=64)] = None,
    alert_id: Annotated[str | None, Query(alias="alertId", max_length=64)] = None,
    caller_kind: Annotated[str | None, Query(alias="callerKind", max_length=16)] = None,
    ai_session_id: Annotated[str | None, Query(alias="aiSessionId", max_length=64)] = None,
) -> Page[dict[str, Any]]:
    """决策记录列表（行级范围同告警：只列可见对象的记录）。"""
    return await svc.list(
        page,
        obj_kind=obj_kind,
        obj_id=obj_id,
        alert_id=alert_id,
        caller_kind=caller_kind,
        ai_session_id=ai_session_id,
    )


@router.get("/decisions/{code}", dependencies=ALERTS_READ)
async def get_decision(
    code: Annotated[str, Path(max_length=64, pattern=r"^[^\x00]+$")],
    svc: Annotated[DecisionService, Depends(_decisions)],
) -> dict[str, Any]:
    return await svc.get(code)


# 告警详情「AI 处置建议」：最近一条关联本告警的决策记录（自动评估 caller.kind = system，或人手动生成）。
@router.get("/alerts/{code}/decision", dependencies=ALERTS_READ)
async def alert_decision(
    code: DynamicPathCode, svc: Annotated[DecisionService, Depends(_decisions)]
) -> dict[str, Any]:
    return {"alertId": code, "decision": await svc.latest_for_alert(code)}


@router.post("/alerts/{code}/decision:evaluate", dependencies=ALERTS_READ)
async def alert_decision_evaluate(
    code: DynamicPathCode, svc: Annotated[DecisionService, Depends(_decisions)]
) -> dict[str, Any]:
    return {"alertId": code, "decision": await svc.evaluate(DecisionEvaluateIn(alert_id=code))}


from app.modules.dynamic.rules_router import router as _rules_router  # noqa: E402  第二部分：规则 / 推演 / 生命周期

router.include_router(_rules_router)
