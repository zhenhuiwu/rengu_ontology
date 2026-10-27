"""admin 模块 seed（JSON 组 system）。

- json/system/ADMIN_USERS.json → sys_user：120 个用户，code = id（U010231…），状态中文 → 英文 code，
  role、createdAt → role、joined_on（admin_0002）。
- extra/system/SYS_USERS.json → sys_user：mock 里出现、但不在 ADMIN_USERS 的人
  （演示身份王敏 admin / 李娜 viewer、版本发布人）。
  - 编号能在 mock 审计日志里找到的照抄：王敏 U010001、陈立军 U010002、何静 U010003、刘婷 U010004；
  - 孙浩然、李娜 mock 里没有编号，[推断] 补 U010005、U010006
    （ontology_release.published_by_id、X-Demo-As 要用）；
  - 赵蕾 U010007、吴佳 U010008：[推断] 非管理员演示人物，mock 里没有编号。
  - 苏刚 U010231（前端 `?previewAs=` 默认预览对象）已在 ADMIN_USERS.json，不在这里重复。
- json/system/AUDIT_LOGS.json → audit_log：521 条，code = id（AL…/ALCR…），
  time 按北京时间解析，结果中文 → 英文 code。
- 写库前 CHECKS 校验 seed 里的 sys_user.name 不重名（动态按姓名比较「本人」），重名直接报错。

`code_by_name(name)`：别的模块 seed 里 mock 只写了人名时（如 RELEASES.by），换成用户编号（重名报错）。
"""

import json
from datetime import date
from functools import lru_cache

from app.common.ontology import DEFAULT_ONTOLOGY_ID
from app.core.config import get_settings
from app.core.schema import parse_display_dt
from app.modules.admin.models import (
    AuditLog,
    AuditResult,
    SysOrg,
    SysRole,
    SysRoleMember,
    SysRoleScope,
    SysTenant,
    SysUser,
    UserStatus,
)
from app.seeds import Ref, Row, SeedContext, SeedSpec, derived_args

JSON_MODULE = "system"


def _user(r: Row, _ctx: SeedContext) -> Row:
    return {
        "code": r["id"],
        "name": r["name"],
        "account": r.get("account"),
        "dept": r.get("dept"),
        "status": UserStatus(r.get("status", "启用")).name,
        "last_login_at": parse_display_dt(r["lastLogin"]) if r.get("lastLogin") else None,
        "role": r.get("role"),
        "joined_on": date.fromisoformat(r["createdAt"]) if r.get("createdAt") else None,
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


def check_unique_names(by_table: dict[str, list[Row]]) -> None:
    """seed 的 sys_user 姓名必须唯一（ADMIN_USERS + SYS_USERS 合起来看）：动态按姓名比较「本人」。"""
    seen: dict[str, list[str]] = {}
    for r in by_table.get(SysUser.__tablename__, []):
        seen.setdefault(r["name"], []).append(r["code"])
    dup = {n: c for n, c in seen.items() if len(c) > 1}
    if dup:
        raise ValueError(f"seed 里 sys_user 姓名重复：{dup}（动态按姓名比较「本人」，姓名必须唯一）")


def _tenant(r: Row, _ctx: SeedContext) -> Row:
    return {
        "code": r["id"],
        "name": r["name"],
        "short_name": r["shortName"],
        "industry": r.get("industry", ""),
    }


def _org(r: Row, ctx: SeedContext) -> Row:
    seq = ctx.cache.setdefault("org_seq", [0])
    seq[0] += 1
    return {"code": r["id"], "name": r["name"], "seq": seq[0] - 1, "is_default": bool(r.get("isDefault"))}


CHECKS = [check_unique_names]

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


def _audit(r: Row, _ctx: SeedContext) -> Row:
    return {
        "code": r["id"],
        "time": parse_display_dt(r["time"]),
        "actor": r["actor"],
        "action": r["action"],
        "target": r["target"],
        "ip": r["ip"],
        "result": AuditResult(r["result"]).name,
    }


SEEDS.append(
    SeedSpec(
        AuditLog,
        "AUDIT_LOGS.json",
        key=("code",),
        transform=_audit,
        description="审计日志（512 条生成 + 9 条变更单固定行 = 521）",
    )
)


# ---- 角色（mock/roles.ts）----
# ROLES.json → sys_role；每个角色的 scopes → sys_role_scope；derived/roleMembers.json → sys_role_member
# （roleMembers(role) 的返回顺序即成员列表顺序）。FUNC_PAGES / PERM_OBJECTS / SCOPE_PROPS 是页面常量，
# GET /roles/meta 直接读 seeds/json/system 下的同名 JSON（与 mock 逐字节一致），不入库。


def _role(r: Row, _ctx: SeedContext) -> Row:
    return {
        "code": r["id"],
        "name": r["name"],
        "description": r.get("desc") or "",
        "built_in": bool(r.get("builtIn")),
        "default_deny": bool(r.get("defaultDeny")),
        "func": r["func"],
        "objs": r["objs"],
        "changed_at": parse_display_dt(r["updatedAt"]),
        "changed_by": r["updatedBy"],
    }


def _role_scopes(r: Row, _ctx: SeedContext) -> list[Row]:
    return [
        {
            # admin_0025: row scopes are per ontology; seed scopes belong to the retail workspace 1.
            "ontology_id": DEFAULT_ONTOLOGY_ID,
            "code": s["id"],
            "role_id": r["id"],
            "seq": i,
            "obj_type": s["objType"],
            "prop": s["prop"],
            "op": s["op"],
            "values": s["values"],
        }
        for i, s in enumerate(r.get("scopes") or [])
    ]


def _role_member(r: Row, ctx: SeedContext) -> Row:
    role = str(derived_args(r)[0])
    counter: dict[str, int] = ctx.cache.setdefault("__role_member_seq__", {})
    seq = counter.get(role, 0)
    counter[role] = seq + 1
    return {"role_id": role, "user_id": r["id"], "seq": seq}


_ROLE = Ref(SysRole, "code", nullable=False)

SEEDS += [
    SeedSpec(SysTenant, "TENANT.json", key=("code",), transform=_tenant, source="extra", description="租户"),
    SeedSpec(SysOrg, "ORGS.json", key=("code",), transform=_org, source="extra", description="组织"),
    SeedSpec(SysRole, "ROLES.json", key=("code",), transform=_role, description="角色"),
    SeedSpec(
        SysRoleScope,
        "ROLES.json",
        key=("ontology_id", "code"),
        transform=_role_scopes,
        refs={"role_id": _ROLE},
        description="角色行级范围",
    ),
    SeedSpec(
        SysRoleMember,
        "derived/roleMembers.json",
        key=("role_id", "user_id"),
        transform=_role_member,
        refs={"role_id": _ROLE, "user_id": Ref(SysUser, "code", nullable=False)},
        description="角色成员（mock roleMembers()）",
    ),
]
