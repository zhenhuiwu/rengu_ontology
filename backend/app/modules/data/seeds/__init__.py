"""data 模块 seed（JSON 组 system，PDE 的 npm run export-mock 生成，原样不改）。

| 导出文件                      | 表               | 说明 |
|-------------------------------|------------------|------|
| system/DATASOURCES.json       | datasource       | 16 行，code = id；枚举存英文 code；无密码 |
| system/DATASOURCES.json       | datasource_run   | 每个数据源的 runs 数组逐条成行，seq = 下标（0 = 最新） |
| system/derived/dsTables.json  | datasource_table | dsTables(ds) 的结果，seq = mock 排序（已映射在前） |

DS_TYPES / DS_DEFAULT_PORT / DM_COMPAT / KB_MODES / ERP_SYSTEMS 是常量，不落表，见 service.META
（tests/test_datasources.py 校验和导出 JSON 一致）。
"""

from datetime import date

from app.core.enums import from_api
from app.core.schema import parse_display_dt
from app.modules.data.models import (
    Datasource,
    DatasourceRun,
    DatasourceTable,
    DsRunResult,
    DsStatus,
    DsSyncMode,
    DsType,
)
from app.seeds import Ref, Row, SeedContext, SeedSpec, derived_args

JSON_MODULE = "system"


def datasource_row(r: Row, _ctx: SeedContext | None = None) -> Row:
    sync = r["sync"]
    return {
        "code": r["id"],
        "name": r["name"],
        "type": from_api(DsType, r["type"]).name,
        "host": r["host"],
        "port": r["port"],
        "database": r["database"],
        "username": r["user"],
        "compat_mode": r.get("compatMode"),
        "database_mode": r.get("databaseMode"),
        "erp_system": r.get("erpSystem"),
        "app_id": r.get("appId"),
        "ssl": r["ssl"],
        "status": from_api(DsStatus, r["status"]).name,
        "last_test": r["lastTest"],
        "owner": r["owner"],
        "object_types": list(r["objectTypes"]),
        "sync_mode": from_api(DsSyncMode, sync["mode"]).name,
        "sync_cron": sync["cron"],
        "sync_cron_text": sync["cronText"],
        "sync_window": sync["window"],
        "sync_cursor_field": sync["cursorField"],
        "sync_next_run": parse_display_dt(sync["nextRun"]) if sync.get("nextRun") else None,
        "table_count": r["tableCount"],
        "created_on": date.fromisoformat(r["createdAt"]),
        "note": r.get("note"),
        "ref_only": r.get("refOnly"),
    }


def run_rows(r: Row, _ctx: SeedContext | None = None) -> list[Row]:
    return [
        {
            "datasource_id": r["id"],
            "seq": k,
            "run_at": parse_display_dt(x["time"]),
            "result": from_api(DsRunResult, x["result"]).name,
            "rows": x["rows"],
            "secs": x["secs"],
            "msg": x["msg"],
        }
        for k, x in enumerate(r.get("runs") or [])
    ]


def table_row(r: Row, ctx: SeedContext) -> Row:
    code = str(derived_args(r)[0])
    # 派生文件逐项展开后没有下标：按同一数据源已出现的行数编号（loader 按文件顺序调用 transform）
    counter: dict[str, int] = ctx.cache.setdefault("__ds_table_seq__", {})
    seq = counter.get(code, 0)
    counter[code] = seq + 1
    return {
        "datasource_id": code,
        "seq": seq,
        "schema_name": r["schema"],
        "name": r["name"],
        "comment": r["comment"],
        "row_count": r["rows"],
        "col_count": r["cols"],
        "object_type": r.get("objectType"),
        "ref": r.get("ref"),
        "meta_updated_at": parse_display_dt(r["updatedAt"]),
    }


_DS = Ref(Datasource, "code", nullable=False)

SEEDS: list[SeedSpec] = [
    SeedSpec(Datasource, "DATASOURCES.json", key=("code",), transform=datasource_row, description="数据连接"),
    SeedSpec(
        DatasourceRun,
        "DATASOURCES.json",
        key=("datasource_id", "seq"),
        transform=run_rows,
        refs={"datasource_id": _DS},
        description="数据连接同步记录",
    ),
    SeedSpec(
        DatasourceTable,
        "derived/dsTables.json",
        key=("datasource_id", "schema_name", "name"),
        transform=table_row,
        refs={"datasource_id": _DS},
        description="数据连接库表清单",
    ),
]
