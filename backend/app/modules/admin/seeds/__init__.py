"""admin 模块 seed（JSON 组 system）。

- json/system/ADMIN_USERS.json → sys_user：120 个用户，code = id（U010231…），状态中文 → 英文 code。
- extra/system/SYS_USERS.json → sys_user：mock 里出现、但不在 ADMIN_USERS 的人
  （演示身份王敏 admin / 李娜 viewer、版本发布人）。
  - 编号能在 mock 审计日志里找到的照抄：王敏 U010001、陈立军 U010002、何静 U010003、刘婷 U010004；
  - 孙浩然、李娜 mock 里没有编号，[推断] 补 U010005、U010006
    （ontology_release.published_by_id、X-Demo-As 要用）。

`code_by_name(name)`：别的模块 seed 里 mock 只写了人名时（如 RELEASES.by），换成用户编号（重名报错）。
"""

import json
from functools import lru_cache

from app.core.config import get_settings
from app.core.schema import parse_display_dt
from app.modules.admin.models import SysUser, UserStatus
from app.seeds import Row, SeedContext, SeedSpec

JSON_MODULE = "system"


def _user(r: Row, _ctx: SeedContext) -> Row:
    return {
        "code": r["id"],
        "name": r["name"],
        "account": r.get("account"),
        "dept": r.get("dept"),
        "status": UserStatus(r.get("status", "启用")).name,
        "last_login_at": parse_display_dt(r["lastLogin"]) if r.get("lastLogin") else None,
    }


@lru_cache
def _names() -> dict[str, list[str]]:
    s = get_settings()
    out: dict[str, list[str]] = {}
    for path in (
        s.seeds_json_dir / "system" / "ADMIN_USERS.json",
        s.seeds_extra_dir / "system" / "SYS_USERS.json",
    ):
        if path.exists():
            for u in json.loads(path.read_text(encoding="utf-8")):
                out.setdefault(u["name"], []).append(u["id"])
    return out


def code_by_name(name: str) -> str:
    codes = _names().get(name, [])
    if len(codes) != 1:
        raise ValueError(
            f"seed 人名 {name!r} 对应 {len(codes)} 个用户编号：{codes}（在 extra/system/SYS_USERS.json 补）"
        )
    return codes[0]


SEEDS: list[SeedSpec] = [
    SeedSpec(SysUser, "ADMIN_USERS.json", key=("code",), transform=_user, description="用户"),
    SeedSpec(
        SysUser,
        "SYS_USERS.json",
        key=("code",),
        transform=_user,
        source="extra",
        description="mock 里出现但不在 ADMIN_USERS 的人（演示身份、发布人）",
    ),
]
