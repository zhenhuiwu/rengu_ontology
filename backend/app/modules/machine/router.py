"""机器接口（第 3 期）：对象查询、函数调用别名、AI 工具清单。挂在 /api/ontology/v1 下。

- `POST /machine/objects:query`（objects:read）：复用使用区对象搜索的绑定、筛选和行级范围
  （qa.explore_api 的 Bound.stmt(viewer) = 行级过滤 + 对象读权限）；服务账号按绑定角色的行级范围。
- `POST /machine/functions/{code}:invoke`（functions:invoke）：复用函数测试运行（runtime.run），
  只允许已发布函数。
- `GET /machine/tools?format=mcp|openai`（meta:read）：能力目录（/meta/capabilities）+ 事件类型
  → 工具 schema。
"""

import time
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import Field
from sqlalchemy import asc, desc, func, select

from app.common.auth import CurrentUserDep, check_func, require_func
from app.core import clock
from app.core.errors import AppError
from app.core.schema import _fmt_dt_seconds
from app.db.session import SessionDep
from app.modules.admin.api_keys import require_api_scope
from app.modules.functions import runtime, service
from app.modules.functions.models import Function
from app.modules.functions.router import path_code
from app.modules.functions.schemas import TestIn
from app.modules.machine import tools
from app.modules.qa.explore_api import ExploreIn, _bind, _facets, _item
from app.modules.qa.object_source import check_object_read

router = APIRouter(tags=["machine"])


class ObjectsQueryIn(ExploreIn):
    object_type: str = Field(min_length=1, max_length=64, pattern=r"^[\w-]+$", description="对象类型编码")
    with_facets: bool = False


@router.post(
    "/machine/objects:query",
    dependencies=[Depends(require_api_scope("objects:read"))],
    summary="机器接口：按对象类型查询（筛选 / 排序 / 分页，行级范围同使用区对象搜索）",
)
async def objects_query(body: ObjectsQueryIn, session: SessionDep, user: CurrentUserDep) -> dict:
    check_object_read(user)
    check_func(user, "object-search", "view")
    t, bd = await _bind(session, body.object_type, None)
    title = next((p.api_name for p in t.properties if p.is_title), t.pk_property)
    out = {
        "objectType": body.object_type,
        "items": [],
        "total": 0,
        "page": 1,
        "size": body.size,
        "titleProp": title,
        "bound": False,
        "boundProps": [],
        "facets": [],
    }
    if bd is None:
        return out
    bd.check(body.filters, "筛选")
    if body.sort:
        bd.check([body.sort.removeprefix("-")], "排序")
    base = await bd.stmt(user)
    stmt = bd.where(base, body)
    if body.sort:
        c = bd.cols[body.sort.removeprefix("-")]
        stmt = stmt.order_by((desc if body.sort.startswith("-") else asc)(c).nulls_last(), bd.id_col)
    else:
        stmt = stmt.order_by(bd.id_col)
    async with bd.read(session) as reader:
        total = (await reader.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
        page = min(body.page, max(1, -(-total // body.size)))
        rows = (await reader.execute(stmt.offset((page - 1) * body.size).limit(body.size))).mappings().all()
        facets = await _facets(reader, bd, base, body) if body.with_facets else []
    return {
        **out,
        "items": [_item(bd, r) for r in rows],
        "total": total,
        "page": page,
        "bound": True,
        "boundProps": bd.out,
        "facets": facets,
    }


@router.post(
    "/machine/functions/{code}:invoke",
    dependencies=[Depends(require_api_scope("functions:invoke")), Depends(require_func("functions", "view"))],
    summary="机器接口：调用已发布函数（同函数测试运行）",
)
async def function_invoke(code: str, body: TestIn, session: SessionDep) -> dict:
    code = path_code(code)
    status = await session.scalar(select(Function.status).where(Function.code == code))
    if status != "published":
        raise AppError(
            "函数不存在或未发布", code="FUNCTION_NOT_PUBLISHED", status_code=404 if status is None else 409
        )
    fn = await service.detail(session, code)
    start = time.perf_counter()
    output = runtime.run(fn["code"], fn["name"], body.input, fn["timeoutS"])
    return {
        "ok": True,
        "functionId": code,
        "version": fn["version"],
        "output": output,
        "ms": max(0, round((time.perf_counter() - start) * 1000)),
        "at": _fmt_dt_seconds(clock.now()),
    }


@router.get(
    "/machine/tools",
    dependencies=[Depends(require_api_scope("meta:read"))],
    summary="机器接口：当前调用方可用的动作 / 事件 / 函数工具清单（MCP / OpenAI 格式）",
)
async def machine_tools(
    request: Request,
    session: SessionDep,
    user: CurrentUserDep,
    format: Literal["mcp", "openai"] = Query("mcp"),
) -> dict:
    return await tools.export(session, user, request, format)
