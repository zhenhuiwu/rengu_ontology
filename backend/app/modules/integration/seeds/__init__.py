"""integration 模块 seed（JSON 组 system）。

本阶段（`integration`，排在 versioning、action 之前）只写 ext_system；writeback_call、reconcile_task
在后面的 `integration_runs` 阶段写（app/modules/integration/seeds/runs.py，排在 dynamic 之后）。

| 导出文件                 | 表         | 说明 |
|--------------------------|------------|------|
| system/EXT_SYSTEMS.json  | ext_system | 10 行，code = id（EXT-CRM…）；状态中文 → 英文 code；不存任何密钥 |
"""

from app.core.enums import from_api
from app.modules.integration.models import ExtSystem, ExtSystemStatus
from app.seeds import Row, SeedContext, SeedSpec

JSON_MODULE = "system"


def ext_system_row(r: Row, _ctx: SeedContext | None = None) -> Row:
    return {
        "code": r["id"],
        "name": r["name"],
        "vendor": r["vendor"],
        "category": r["category"],
        "endpoint": r["endpoint"],
        "auth_type": r["auth"],
        "auth_detail": r["authDetail"],
        "idempotent": r["idempotent"],
        "idem_header": r["idemKey"],
        "timeout_ms": r["timeoutMs"],
        "max_retry": r["maxRetry"],
        "retry_text": r["retry"],
        "rate_limit": r["rateLimit"],
        "owner": r["owner"],
        "status": from_api(ExtSystemStatus, r["status"]).name,
        "env": r["env"],
        "usage": r.get("usage"),
    }


SEEDS: list[SeedSpec] = [
    SeedSpec(ExtSystem, "EXT_SYSTEMS.json", key=("code",), transform=ext_system_row, description="外部系统"),
]
