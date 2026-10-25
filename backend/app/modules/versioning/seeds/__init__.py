"""versioning 模块 seed：导出文件 → 表。

数据：backend/seeds/json/static/（export-mock 的 static 组，文件名 = mock 导出名）
| 导出文件                    | 表               | 说明 |
|-----------------------------|------------------|------|
| static/RELEASES.json        | ontology_release | 每条一行，业务键 ver；parent_id 由上一版的 ver 换成 id |
| static/CHANGE_REQUESTS.json | —（只读关联）    | 已发布且 release=该版本的单号 → change_order_ids |

发布人：RELEASES.by 是人名，按 admin.seeds.code_by_name 换成用户编号，再由 loader 换成 sys_user.id
（admin 阶段在前）。

快照：按各模块登记的 section builder 的 seed_section(ver, ctx) 组装（app/modules/versioning/sections.py），
**不写空快照 {}**；seed_section 只读导出 JSON、不读表，所以 SEED_MODULES 里 versioning 在 static 之前
（静态的 release_id 外键按 ver 换 id，要求版本行先写）。
还没有模块登记 section 时，这个 seed 会明确报错。（变更单本身由静态 seed。）
"""

from app.core.schema import parse_display_dt
from app.modules.admin.models import SysUser
from app.modules.admin.seeds import code_by_name
from app.modules.versioning.models import OntologyRelease
from app.modules.versioning.sections import seed_snapshot
from app.modules.versioning.service import snapshot_digest
from app.seeds import Ref, Row, SeedContext, SeedSpec


def _release(r: Row, ctx: SeedContext) -> Row:
    releases = ctx.load("RELEASES.json")
    # RELEASES 新→旧排列，下一条就是上一版
    idx = next(i for i, x in enumerate(releases) if x["ver"] == r["ver"])
    parent = releases[idx + 1]["ver"] if idx + 1 < len(releases) else None
    crs = [
        c["id"]
        for c in ctx.load("CHANGE_REQUESTS.json")
        if c.get("status") == "已发布" and c.get("release") == r["ver"]
    ]
    snapshot = seed_snapshot(r["ver"], ctx)
    return {
        "ver": r["ver"],
        "parent_id": parent,  # 先写版本号，loader 按 refs 换成 id（自引用：上一版先写入）
        "kind": "publish",  # upsert 直接写 DB code
        "snapshot": snapshot,
        "snapshot_sha256": snapshot_digest(snapshot),
        "change_order_ids": sorted(crs),
        "change_summary": {"changeOrders": len(crs), "seeded": True},
        "note": r["note"],
        "published_by_id": code_by_name(r["by"]),  # 人名 → 用户编号，loader 再换成 sys_user.id
        "published_at": parse_display_dt(r["publishedAt"]),
    }


JSON_MODULE = "static"

SEEDS = [
    SeedSpec(
        OntologyRelease,
        "RELEASES.json",
        key=("ver",),
        transform=_release,
        description="本体发布记录",
        refs={"parent_id": Ref(OntologyRelease, "ver"), "published_by_id": Ref(SysUser, "code")},
    ),
]
