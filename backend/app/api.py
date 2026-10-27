"""路由挂载。前缀按分组：/api/<group>/v1（PDE api-index v0.2）。代码目录按模块，挂载按分组。

| 模块目录          | 分组前缀            | 负责人            |
|-------------------|---------------------|-------------------|
| versioning/static | /api/ontology/v1    | 本体小白 / 静态   |
| dynamic/action/event | /api/ontology/v1 | 动态 / Palantir   |
| qa                | /api/qa/v1          | 问数调研员        |
| data              | /api/data/v1        | 待定（数据连接）  |
| integration       | /api/data/v1（/ext-systems、/writeback-calls；不在 ontology 下开路由） | Palantir专家 |
| biz               | /api/biz/v1         | 待定（演示业务数据：门店/商品/订单） |

每个分组路由都挂全局只读守卫（app.common.context.readonly_guard）：带 ?ver / ?previewAs 的写请求 403。
| admin             | /api/admin/v1       | PDE（用户、角色、API Key、配置、审计） |
"""

from fastapi import APIRouter, FastAPI

from app.common.context import ReadonlyGuardDep
from app.common.auth import ApiKeyGuardDep
from app.core.config import api_prefix
from app.modules.action.router import router as action_router
from app.modules.admin.router import router as admin_router
from app.modules.admin.api_keys import router as api_keys_router
from app.modules.biz.router import router as biz_router
from app.modules.dashboard.router import router as dashboard_router
from app.modules.data.router import router as data_router
from app.modules.dynamic.router import router as dynamic_router
from app.modules.event.ingress import router as internal_event_router
from app.modules.event.router import router as event_router
from app.modules.functions.router import router as functions_router
from app.modules.integration.router import router as integration_router
from app.modules.qa.router import router as qa_router
from app.modules.static.router import router as static_router
from app.modules.versioning.router import router as versioning_router

MOUNTS: list[tuple[str, APIRouter]] = [
    ("ontology", versioning_router),
    ("ontology", static_router),
    ("ontology", dynamic_router),
    ("ontology", action_router),
    ("ontology", event_router),
    ("ontology", functions_router),
    ("qa", qa_router),
    ("data", data_router),
    ("data", integration_router),  # integration 只挂 data（action-event-api.md:140、:168）
    ("biz", biz_router),
    ("biz", dashboard_router),  # 首页看板 /dashboard/kpis、/stores/ranking（PDE）
    ("admin", admin_router),
    ("admin", api_keys_router),
]


def include_routers(app: FastAPI) -> None:
    for module, router in MOUNTS:
        app.include_router(router, prefix=api_prefix(module), dependencies=[ReadonlyGuardDep, ApiKeyGuardDep])
    app.include_router(internal_event_router, prefix="/internal/v1", dependencies=[ReadonlyGuardDep])
