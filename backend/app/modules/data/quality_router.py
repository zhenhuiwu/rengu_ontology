"""质量检查：只接受固定映射，运行在当前事务同步完成。"""

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import func, select

from app.common.auth import CurrentUserDep, check_func, require_func, resolve_user
from app.common.context import ViewContextDep
from app.common.pagination import PageParamsDep
from app.core.errors import AppError, EmptyInput
from app.db.session import SessionDep
from app.modules.data import quality as service
from app.modules.data import quality_cr
from app.modules.data.quality_models import QualityRun
from app.modules.data.quality_schemas import ChangeOptions, CheckSave

router = APIRouter(tags=["data-quality"])
VIEW = [Depends(require_func("data-quality", "view"))]
MANAGE = [Depends(require_func("data-quality", "manage"))]
Code = Annotated[str, Path(pattern=r"^[^\x00]+$")]


async def owner(session, user, ctx):
    effective = await resolve_user(session, ctx.preview_as) if ctx.preview_as else user
    check_func(effective, "data-quality", "view")
    return effective.id


@router.get("/quality-checks/meta", dependencies=VIEW)
async def meta(session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep):
    await owner(session, user, ctx)
    return {"objects": await service.meta(session, ctx.ver), "automaticScheduling": False}


@router.get("/quality-checks", dependencies=VIEW)
async def list_checks(
    session: SessionDep,
    user: CurrentUserDep,
    ctx: ViewContextDep,
    page: PageParamsDep,
    type_key: Annotated[str | None, Query(alias="typeKey")] = None,
    kind: str | None = None,
    result: str | None = None,
):
    return await service.list_checks(
        session, await owner(session, user, ctx), page, type_key, kind, result, ctx.ver
    )


@router.post("/quality-checks", dependencies=MANAGE, status_code=201)
async def create(body: CheckSave, session: SessionDep, user: CurrentUserDep):
    if not body.name.strip():
        raise AppError("名称不能为空", code="VALIDATION_FAILED", status_code=422)
    return await quality_cr.stage(
        session,
        user,
        body,
        body.model_dump(by_alias=True, include={"name", "type_key", "kind", "threshold", "schedule"}),
    )


@router.put("/quality-checks/{code}", dependencies=MANAGE)
async def update(code: Code, body: CheckSave, session: SessionDep, user: CurrentUserDep):
    value = body.model_dump(by_alias=True, include={"name", "type_key", "kind", "threshold", "schedule"})
    return await quality_cr.stage(session, user, body, value, code)


@router.delete("/quality-checks/{code}", dependencies=MANAGE)
@router.post("/quality-checks/{code}:retire", dependencies=MANAGE)
async def retire(code: Code, body: ChangeOptions, session: SessionDep, user: CurrentUserDep):
    return await quality_cr.stage(session, user, body, code=code, retire=True)


@router.get("/quality-checks/{code}/runs", dependencies=VIEW)
async def runs(
    code: Code, session: SessionDep, user: CurrentUserDep, ctx: ViewContextDep, page: PageParamsDep
):
    user_id = await owner(session, user, ctx)
    if ctx.ver and code not in await service.definitions(session, user_id, ctx.ver):
        from app.core.errors import NotFound

        raise NotFound("质量检查不在该历史版本中")
    check, _ = await service.get_check(session, user_id, code)
    query = select(QualityRun).where(QualityRun.check_id == check.id).order_by(QualityRun.id.desc())
    if ctx.ver:
        from app.modules.versioning.repo import ReleaseRepo

        release = await ReleaseRepo(session).by_ver_or_404(ctx.ver)
        query = query.where(QualityRun.time <= release.published_at)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    p = page.clamp(total)
    rows = list(await session.scalars(query.offset(p.offset).limit(p.size)))
    return {
        "items": [service.run_out(r) for r in rows],
        "total": total,
        "page": p.page,
        "size": p.size,
    }


@router.post("/quality-checks/{code}:run", dependencies=MANAGE)
async def run(code: Code, session: SessionDep, user: CurrentUserDep):
    job_id = f"QJ-{uuid4().hex}"
    result = await service.execute_check(session, user, code, job_id)
    return {"jobId": job_id, "completed": True, "run": service.run_out(result)}


@router.post("/quality-checks:run-all", dependencies=MANAGE)
async def run_all(session: SessionDep, user: CurrentUserDep):
    checks = (await session.scalars(service.checks_query(user.id).order_by("id").limit(101))).all()
    if not checks:
        raise EmptyInput("请先新建质量检查")
    if len(checks) > 100:
        raise AppError("单次最多运行100个检查，请逐项运行", code="QUALITY_BATCH_LIMIT", status_code=422)
    job_id = f"QJ-{uuid4().hex}"
    for check in checks:
        await service.execute_check(session, user, check.code, job_id)
    return {"jobId": job_id, "completed": True, "count": len(checks), "estimate": "已完成"}
