"""versioning 模块 seed。

数据：backend/seeds/json/versioning/（PDE 的 export-mock 生成；仓库里附了一份小样例）
- releases.json        ← 前端 src/mock/releases.ts 的 RELEASES（原样）
- change_requests.json ← src/mock/changes.ts 的 CHANGE_REQUESTS（这里只用 status=已发布 的 id↔release 关联；
                          变更单本身由静态模块 seed）

mock 里的版本没有快照内容（静态定义的快照要等静态模块的 seed 落库后才能拼出来），
所以 seed 写入空快照 {}；变更单号从 changes.ts 的 CHANGE_REQUESTS（status=已发布、release=该版本）关联出来。
"""

from app.core.schema import parse_display_dt
from app.modules.versioning.models import OntRelease
from app.modules.versioning.service import snapshot_digest
from app.seeds import Row, SeedContext, SeedSpec


def _release(r: Row, ctx: SeedContext) -> Row:
    releases = ctx.load("releases.json")
    # RELEASES 新→旧排列，下一条就是上一版
    idx = next(i for i, x in enumerate(releases) if x["ver"] == r["ver"])
    parent = releases[idx + 1]["ver"] if idx + 1 < len(releases) else None
    crs = [
        c["id"]
        for c in ctx.load("change_requests.json")
        if c.get("status") == "已发布" and c.get("release") == r["ver"]
    ]
    return {
        "ver": r["ver"],
        "parent_ver": parent,
        "kind": "publish",  # upsert 直接写 DB code
        "snapshot": {},
        "snapshot_sha256": snapshot_digest({}),
        "change_order_ids": sorted(crs),
        "change_summary": {"changeOrders": len(crs), "seeded": True},
        "note": r["note"],
        "published_by": r["by"],
        "published_at": parse_display_dt(r["publishedAt"]),
    }


JSON_MODULE = "versioning"

SEEDS = [
    SeedSpec(OntRelease, "releases.json", key=("ver",), transform=_release, description="本体发布记录"),
]
