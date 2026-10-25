"""integration（负责人 Palantir专家）。两个路由，挂在不同分组下（见 app/api.py）：

- ext_router → /api/data/v1：外部系统管理（/ext-systems，属于数据连接分组的页面）；
- router     → /api/ontology/v1：动作执行记录、回写调用、事件、对账（/action-runs、/reconcile-tasks 等）。

冒号动作按 PDE api-index：`POST /action-runs/{id}:submit`、`:reconcile` 等。
"""

from fastapi import APIRouter

ext_router = APIRouter(tags=["integration"])  # /api/data/v1
router = APIRouter(tags=["integration"])  # /api/ontology/v1
