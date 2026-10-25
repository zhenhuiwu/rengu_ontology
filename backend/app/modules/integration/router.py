"""integration（负责人 Palantir专家）。只有一个路由，挂在 /api/data/v1（见 app/api.py）：

- 外部系统 /ext-systems*、回写调用 /writeback-calls*
  （action-event-api.md:140、:168，PDE 定外部系统属数据分组）。
- integration **不在 /api/ontology/v1 下开任何路由**（Palantir 定）：动作类型、事件类型、事件、对账等
  /api/ontology/v1 接口由 action / event 模块的路由提供，执行记录由 dynamic 提供。

冒号动作按 PDE api-index：`POST /ext-systems/{id}:test` 等。
"""

from fastapi import APIRouter

router = APIRouter(tags=["integration"])  # /api/data/v1
