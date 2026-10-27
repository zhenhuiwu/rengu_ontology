"""演示主线种子补丁（seed 阶段 `demo_mainline`，排在 dynamic 之后；负责模块 admin）。

主线：库存告警 AL-20261025-0139（规则 R-012）→ 补货申请 request_replenish → 人工确认 → Webhook 投递。
handoff/xiaobai.md §4 经批准的范围，**只新增数据**，不改 seeds/json（export-mock 生成）的任何取值。
数据在 extra/system/DEMO_MAINLINE.json，按顶层键分段：

- roles → sys_role、sys_role_scope：新角色 R-09 供应链计划员（request_replenish 执行人条件里的角色，
  此前「尚未登记的角色」）、R-10 区域供应链经理（approval 文本里的审批角色）；行级范围 门店.大区 = 华东。
  认领告警 / 发起动作要 alerts manage，发起 / 审批动作要 action-center manage。
- roleMembers → sys_role_member：周晓峰 U010011 → R-09（R-012 autoStage.confirmer「供应链计划员 周晓峰」）；
  姚娜 U010812（华东 / 供应链，启用）→ R-10，原有的动作审批人角色保留。不改 sys_user.role 显示值。
- ontologyMembers → ontology_member：成员只在迁移 ontology_0018 里按当时已有的用户生成，
  全新库 migrate 后再 seed 一个成员都没有；这里给主线两人补本体 1 editor（审批人必须 owner/editor）。
- ontologyRoleBindings → ontology_role_binding：同理绑定只在迁移里生成；R-06 门店店长、R-09、R-10
  绑定本体 1（对象授权、角色条件都只认已绑定的角色）。
- extSystems → ext_system：EXT-WEBHOOK，类别 Webhook，地址是本机 https://127.0.0.1:8443
  （适配器只接受 HTTPS），不存任何密钥；路径 / 签名密钥走 RENGU_INTEGRATION_ADAPTERS（1/EXT-WEBHOOK）。
- alertAssignees → alert.assignee：演示告警处理人 = U010011（已批准）；只改 assignee，
  其余列按 ALERTS.json 原值重写（upsert 要整行）。

不在这里做（会改既有取值）：request_replenish.approval 文本（改成能定位到用户编号）、
sysId 改到 EXT-WEBHOOK、告警状态；也不建服务账号、不绑定 R-019。
"""

from app.common.ontology import DEFAULT_ONTOLOGY_ID
from app.core.config import get_settings
from app.modules.admin.models import SysRole, SysRoleMember, SysRoleScope, SysUser
from app.modules.admin.seeds import _role, _role_scopes
from app.modules.dynamic.models import Alert
from app.modules.integration.models import ExtSystem
from app.modules.integration.seeds import ext_system_row
from app.modules.ontology.models import OntologyMember, OntologyRoleBinding
from app.seeds import Ref, Row, SeedContext, SeedSpec

JSON_MODULE = "system"
FILE = "DEMO_MAINLINE.json"

_ROLE = Ref(SysRole, "code", nullable=False)
_USER = Ref(SysUser, "code", nullable=False)


def _ontology(r: Row) -> int:
    if r["ontology"] != DEFAULT_ONTOLOGY_ID:
        raise ValueError(f"演示主线只补零售本体 {DEFAULT_ONTOLOGY_ID}：{r}")
    return r["ontology"]


def _role_member(r: Row, ctx: SeedContext) -> Row:
    counter: dict[str, int] = ctx.cache.setdefault("__demo_role_member_seq__", {})
    seq = counter.get(r["role"], 0)
    counter[r["role"]] = seq + 1
    return {"role_id": r["role"], "user_id": r["user"], "seq": seq}


def _member(r: Row, _ctx: SeedContext) -> Row:
    return {"ontology_id": _ontology(r), "user_id": r["user"], "role": r["role"]}


def _binding(r: Row, _ctx: SeedContext) -> Row:
    return {"ontology_id": _ontology(r), "role_id": r["role"]}


def _alert_assignee(r: Row, ctx: SeedContext) -> Row:
    from app.modules.dynamic.seeds import _alert

    if "__demo_dyn__" not in ctx.cache:
        root = get_settings().seeds_json_dir
        dyn = SeedContext(root / "dynamic", root=root)  # _alert 要读 dynamic 组的告警时间线
        ctx.cache["__demo_dyn__"] = (dyn, {a["id"]: a for a in dyn.load("ALERTS.json")})
    dyn, alerts = ctx.cache["__demo_dyn__"]
    src = alerts.get(r["alert"])
    if src is None:
        raise ValueError(f"演示告警不在 ALERTS.json：{r['alert']}")
    return {**_alert(dict(src), dyn), "assignee": r["assignee"]}


def _spec(model, path: str, key: tuple[str, ...], transform, refs=None, description: str = "") -> SeedSpec:
    return SeedSpec(
        model,
        FILE,
        key=key,
        transform=transform,
        path=path,
        source="extra",
        refs=refs or {},
        description=description,
    )


SEEDS: list[SeedSpec] = [
    _spec(SysRole, "roles", ("code",), _role, description="演示主线角色"),
    _spec(
        SysRoleScope,
        "roles",
        ("ontology_id", "code"),
        _role_scopes,
        {"role_id": _ROLE},
        "演示主线角色行级范围",
    ),
    _spec(
        SysRoleMember,
        "roleMembers",
        ("role_id", "user_id"),
        _role_member,
        {"role_id": _ROLE, "user_id": _USER},
        "演示主线角色成员",
    ),
    _spec(
        OntologyMember, "ontologyMembers", ("ontology_id", "user_id"), _member, {"user_id": _USER}, "本体成员"
    ),
    _spec(
        OntologyRoleBinding,
        "ontologyRoleBindings",
        ("ontology_id", "role_id"),
        _binding,
        {"role_id": _ROLE},
        "本体角色绑定",
    ),
    _spec(
        ExtSystem, "extSystems", ("code",), ext_system_row, description="Webhook 外部系统（本机地址，无密钥）"
    ),
    _spec(Alert, "alertAssignees", ("code",), _alert_assignee, description="演示告警处理人"),
]
