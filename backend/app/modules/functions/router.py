"""Real function metadata, CR writes, and bounded no-side-effect test execution."""

import time

from fastapi import APIRouter, Depends, Query

from app.common.auth import CurrentUserDep, require_func
from app.common.ontology import require_ontology_id
from app.common.pagination import PageParams
from app.core import clock
from app.core.errors import AppError
from app.core.schema import _fmt_dt_seconds
from app.db.session import SessionDep
from app.modules.admin.api_keys import require_api_scope
from app.modules.functions import runtime, service
from app.modules.functions.executor import authorize, invoke_snapshot
from app.modules.functions.remote import targets
from app.modules.functions.schemas import CreateIn, RollbackIn, SaveIn, TestIn

router = APIRouter(tags=["functions"], dependencies=[Depends(require_func("functions", "view"))])
Manage = Depends(require_func("functions", "manage"))


def path_code(code: str) -> str:
    if len(code) > 64 or not code or "\x00" in code:
        raise AppError("无效函数编号", code="VALIDATION_FAILED", status_code=422)
    runtime.json_value(code)
    return code


@router.get("/functions")
async def list_functions(
    session: SessionDep,
    kw: str = Query("", max_length=128),
    status: str = Query("全部", max_length=64),
    page: int = 1,
    size: int = Query(10, ge=1, le=200),
) -> dict:
    runtime.json_value(kw)
    runtime.json_value(status)
    return await service.list_page(session, kw, status, PageParams(page=page, size=size))


@router.post("/functions", status_code=201, dependencies=[Manage])
async def create_function(body: CreateIn, session: SessionDep, user: CurrentUserDep) -> dict:
    return await service.save(session, user, body)


@router.get("/functions/{code}/versions")
async def versions(
    code: str, session: SessionDep, page: int = 1, size: int = Query(10, ge=1, le=200)
) -> dict:
    return await service.history(session, path_code(code), PageParams(page=page, size=size))


@router.post(
    "/functions/{code}:test",
    dependencies=[Depends(require_api_scope("functions:invoke", function_type="{code}"))],
)
async def test_function(code: str, body: TestIn, session: SessionDep, user: CurrentUserDep) -> dict:
    fn = await service.detail(session, path_code(code))
    start = time.perf_counter()
    if body.version:
        from app.contracts.functions import invoke_version
        output = await invoke_version(session, user, code, body.version, body.input, strict_local=False)
    else:
        await authorize(session, user, code)
        output = await invoke_snapshot(fn, body.input, ontology_id=require_ontology_id(session),
                                       code=code, version=fn["version"])
    return {
        "output": output,
        "ms": max(0, round((time.perf_counter() - start) * 1000)),
        "at": _fmt_dt_seconds(clock.now()),
    }


@router.post("/functions/{code}:rollback", dependencies=[Manage])
async def rollback(code: str, body: RollbackIn, session: SessionDep, user: CurrentUserDep) -> dict:
    return await service.rollback(session, user, path_code(code), body)


@router.put("/functions/{code}", dependencies=[Manage])
async def update_function(code: str, body: SaveIn, session: SessionDep, user: CurrentUserDep) -> dict:
    return await service.save(session, user, body, path_code(code))


@router.get("/function-targets", dependencies=[Depends(require_api_scope("meta:read"))])
async def function_targets(session: SessionDep) -> dict:
    return {"items": [target.public() for target in targets(require_ontology_id(session))]}


@router.get("/functions/{code}")
async def detail(code: str, session: SessionDep) -> dict:
    return await service.detail(session, path_code(code))
