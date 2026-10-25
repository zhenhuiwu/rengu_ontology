"""integration 的第二段 seed（阶段 `integration_runs`，见 app/seeds/registry.py）。负责人：Palantir。

写 writeback_call：`action_run_id` 指向动态的 action_run，所以排在 dynamic 之后、qa 之前。
ext_system 仍由 `integration` 阶段（app/modules/integration/seeds/__init__.py）写，排在前面。

| 导出文件                          | 表             | 说明 |
|-----------------------------------|----------------|------|
| system/derived/extCalls.json      | writeback_call | 最近调用，756 行 |

kind【推断】：GET → query，其余 → write（mock 没有这个字段；测试调用由 `:test` 写，kind = test）。
reconcile_task 后续批次再加。
"""

from app.core.enums import from_api
from app.core.schema import parse_display_dt
from app.modules.dynamic.models import ActionRun
from app.modules.integration.models import CallKind, CallResult, ExtSystem, WritebackCall
from app.seeds import Ref, Row, SeedContext, SeedSpec

JSON_MODULE = "system"


def writeback_call_row(r: Row, _ctx: SeedContext | None = None) -> Row:
    return {
        "code": r["id"],
        "sys_id": r["sysId"],
        "action_run_id": r.get("runId"),
        "kind": (CallKind.query if r["method"] == "GET" else CallKind.write).name,
        "at": parse_display_dt(r["time"]),
        "op_name": r["action"],
        "target_label": r["target"],
        "method": r["method"],
        "path": r["path"],
        "idem_key": r["idemKey"],
        "elapsed_ms": r["ms"],
        "http_status": r["http"],
        "result": from_api(CallResult, r["result"]).name,
        "attempt": r["attempt"],
        "actor": r["actor"],
        "msg": r["msg"],
    }


SEEDS: list[SeedSpec] = [
    SeedSpec(
        WritebackCall,
        "derived/extCalls.json",
        key=("code",),
        transform=writeback_call_row,
        description="外部系统调用记录",
        refs={"sys_id": Ref(ExtSystem, "code", nullable=False), "action_run_id": Ref(ActionRun, "code")},
    ),
]
