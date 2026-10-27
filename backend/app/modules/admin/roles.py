"""角色与权限（Roles.vue，api-index §4.2）：数据访问 + 业务逻辑。不 commit。

- 角色、行级范围、成员三张表（admin_0003）。
- 功能页面读 seeds/json/system；对象和范围属性读已发布本体，空本体用同名 mock JSON 兜底。
- 内置角色：改名、改权限、增删范围、删除一律 403（Roles.vue locked = readonly || builtIn）；成员可以改。
- 有效权限逐行照 mock/roles.ts effectiveAccess()：功能取各角色最高级别；
  对象可见 = 任一角色可查看（defaultDeny 角色没配该对象范围时不算授予）；
  任一授予角色对该对象不限范围 → 「全部」，否则范围并集，用「；或 」连接。
- 每次写操作记一条审计（insert_audit，与业务同一事务）。
- 本文件不改 app/common/auth.py：鉴权仍走 PDE 的 require_func 桩，角色表接入鉴权留到下一阶段。
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser
from app.common.ontology import require_ontology_id
from app.common.pagination import PageParams
from app.common.repository import BaseRepository
from app.core.clock import now
from app.core.config import get_settings
from app.core.errors import AppError, DuplicateKey, Forbidden, NotFound
from app.modules.admin.models import (
    SysOrg,
    SysRole,
    SysRoleMember,
    SysRoleScope,
    SysTenant,
    SysUser,
    UserStatus,
)
from app.modules.admin.repo import insert_audit, lock_code_seq

# ---- 页面常量 ----


@lru_cache
def _meta_json(name: str) -> list[dict[str, Any]]:
    return json.loads((get_settings().seeds_json_dir / "system" / f"{name}.json").read_text(encoding="utf-8"))


def func_pages() -> list[dict[str, Any]]:
    return _meta_json("FUNC_PAGES")


def perm_objects() -> list[dict[str, Any]]:
    """兜底清单（mock PERM_OBJECTS 原样）。业务逻辑用 load_perm_objects() 读本体。"""
    return _meta_json("PERM_OBJECTS")


async def load_perm_objects(session: AsyncSession) -> list[dict[str, Any]]:
    """权限对象清单改读本体（只读静态 object_type、Palantir action_type，不改它们的文件）。

    - 对象 = 已发布的对象类型（display_name / api_name，按 seq）；本体里一个都没有 → 用兜底清单。
    - 动作 = 指向该对象 api 的动作类型（草稿除外）∪ 兜底清单里同 api 的动作（去重，保证老角色配置仍合法）。
    - 兜底清单里本体没有的对象（如 告警、员工 这类运行时对象）接在后面，不丢已有配置。
    """
    from app.modules.action.execution_options import action_operation_objects
    from app.modules.action.models import ActionType, TypeStatus
    from app.modules.action.sections import action_def
    from app.modules.static.models import ObjectStatus, ObjectType

    fallback = perm_objects() if require_ontology_id(session) == 1 else []
    ots = (
        await session.execute(
            select(ObjectType.display_name, ObjectType.api_name)
            .where(ObjectType.status == ObjectStatus.published)
            .order_by(ObjectType.seq, ObjectType.code)
        )
    ).all()
    if not ots:
        return fallback
    acts = await session.scalars(
        select(ActionType)
        .where(ActionType.status != TypeStatus.draft, ActionType.retired_release_id.is_(None))
        .order_by(ActionType.code)
    )
    by_api: dict[str, list[str]] = {}
    for action in acts:
        by_api.setdefault(action.target_api, []).append(action.name)
        for obj in await action_operation_objects(session, action_def(action)):
            by_api.setdefault(obj.api_name, []).append(action.name)
    fb = {o["api"]: o for o in fallback}
    out = [
        {
            "name": n,
            "api": a,
            "actions": list(dict.fromkeys([*fb.get(a, {}).get("actions", []), *by_api.get(a, [])])),
        }
        for n, a in ots
    ]
    apis, names = {o["api"] for o in out}, {o["name"] for o in out}
    out += [dict(o) for o in fallback if o["api"] not in apis and o["name"] not in names]
    return out


async def load_scope_props(session: AsyncSession, user: CurrentUser) -> list[dict[str, Any]]:
    """可筛选的离散属性 ∩ 已登记的行级依据；取值与实际执行范围使用同一受控列。

    当前没有独立 permissionBasis 标记，不能把所有 filterable 属性都当作可执行权限依据。
    RowScope 只存字符串且支持 = / ∈，所以不开放数值、时间、布尔和复合类型。
    """
    from app.common.row_filter import apply_row_filter
    from app.common.sql_guard import governed_tables
    from app.modules.static.explorer import facetable
    from app.modules.static.models import ObjectStatus, ObjectType

    objects = list(
        (
            await session.scalars(
                select(ObjectType)
                .where(ObjectType.status == ObjectStatus.published)
                .order_by(ObjectType.seq, ObjectType.code)
            )
        ).all()
    )
    if not objects:
        return _meta_json("SCOPE_PROPS") if require_ontology_id(session) == 1 else []
    governed = governed_tables(require_ontology_id(session)).values()
    tables = {g.object_type: g for g in governed if not g.inherit}
    out = []
    for obj in objects:
        g = tables.get(obj.code)
        if g is None:
            continue
        for p in obj.properties:
            if not facetable(p) or p.data_type not in {"string", "enum"}:
                continue
            # 兼容本体显示名与历史权限名不同（如 所属大区 / 区域），仅按同一受控列匹配。
            basis = next(
                (
                    (name, field)
                    for name, field in g.field_of_prop.items()
                    if name == p.display_name or (p.backing_column and field == p.backing_column)
                ),
                None,
            )
            if basis is None:
                continue
            name, field = basis
            source = g.table
            if "." in field:
                link_id, key = field.split(".", 1)
                link = g.links.get(link_id)
                col = link.target_columns.get(key) if link else None
                if link is not None:
                    source = source.join(link.target, link.target_key == link.local)
            else:
                col = g.columns.get(field)
            if col is None:
                continue
            stmt = select(col).select_from(source).where(col.is_not(None)).distinct()
            stmt = await apply_row_filter(stmt, user, obj.code, g.columns, g.links)
            vals = (await session.scalars(stmt)).all()
            values = {str(getattr(v, "value", v)) for v in vals}
            aliases = g.value_alias.get(name, {})
            out.append(
                {
                    "objType": obj.display_name,
                    "objectTypeCode": obj.code,  # stable reference sent back on add_scope (admin_0025)
                    "prop": name,
                    "displayName": p.display_name,
                    "dataType": p.data_type,
                    # 真实值不能经旧范围别名变成另一取值；旧角色仍由执行器兼容。
                    "values": sorted(v for v in values if aliases.get(v, v) == v),
                }
            )
    return out


async def roles_meta(session: AsyncSession, user: CurrentUser) -> dict[str, Any]:
    po = await load_perm_objects(session)
    return {
        "func_pages": func_pages(),
        "perm_objects": po,
        "scope_props": await load_scope_props(session, user),
        "obj_actions": {o["name"]: o["actions"] for o in po},
    }


# 新建抽屉的权限模板（Roles.vue NR_PERMS）→ 初始 func / objs。
# [推断] 前端只是勾选框、没有落地规则，按名字的字面意思映射：
#   查看对象：对象类型全部可查看，对象搜索 / 详情 = 查看；导出数据：可查看的对象可导出；
#   执行动作：可查看的对象授予全部动作，动作中心 = 管理；发起变更：构建区全部页面 = 管理；
#   审批变更：变更与发布 = 管理；管理用户：用户与组织 = 管理。没勾的一律 none / 不可见。
NR_PERMS = ["查看对象", "导出数据", "执行动作", "发起变更", "审批变更", "管理用户"]
_RANK = {"none": 0, "view": 1, "manage": 2}


def template_perms(
    perms: list[str], po: list[dict[str, Any]] | None = None
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    p = set(perms)
    fn = {pg["key"]: "none" for pg in func_pages()}

    def bump(key: str, lv: str) -> None:
        if _RANK[lv] > _RANK[fn.get(key, "none")]:
            fn[key] = lv

    if "查看对象" in p:
        bump("object-search", "view")
        bump("object-detail", "view")
    if "执行动作" in p:
        bump("action-center", "manage")
    if "发起变更" in p:
        for pg in func_pages():
            if pg["areaKey"] == "build":
                bump(pg["key"], "manage")
    if "审批变更" in p:
        bump("releases", "manage")
    if "管理用户" in p:
        bump("users", "manage")
    view = "查看对象" in p
    objs = [
        {
            "objType": o["name"],
            "api": o["api"],
            "view": view,
            "export": view and "导出数据" in p,
            "actions": list(o["actions"]) if view and "执行动作" in p else [],
        }
        for o in (po if po is not None else perm_objects())
    ]
    return fn, objs


def scope_text(s: SysRoleScope) -> str:
    return f"{s.prop} {s.op} {'、'.join(s.values)}"


def resolve_scope_label(s: SysRoleScope, names: dict[int, str]) -> None:
    """引用对象类型的范围按对象当前显示名匹配；引用已删除（只剩 code）→ 不匹配任何对象（拒绝）。"""
    if s.object_type_id is not None:
        s.__dict__["_label"] = names.get(s.object_type_id, "")
    elif s.object_type_code is not None:
        s.__dict__["_label"] = ""


def scope_label(s: SysRoleScope) -> str:
    """范围匹配用的对象名称；历史数据（未映射对象类型）沿用存储的中文名。"""
    return s.label


# ---- 数据访问 ----


@dataclass
class RoleBundle:
    role: SysRole
    scopes: list[SysRoleScope]
    member_count: int


class SysRoleRepository(BaseRepository[SysRole]):
    model = SysRole
    not_found_message = "角色不存在"

    async def all_roles(self) -> list[SysRole]:
        return list((await self.session.scalars(select(SysRole).order_by(SysRole.code))).all())

    async def scopes_of(self, role_ids: list[int]) -> dict[int, list[SysRoleScope]]:
        """当前本体工作空间的范围（显式条件 + FORCE RLS）；引用对象类型的范围显示对象当前名称。"""
        from app.modules.static.models import ObjectType

        out: dict[int, list[SysRoleScope]] = {i: [] for i in role_ids}
        if role_ids:
            rows = list(
                await self.session.scalars(
                    select(SysRoleScope)
                    .where(
                        SysRoleScope.role_id.in_(role_ids),
                        SysRoleScope.ontology_id == require_ontology_id(self.session),
                    )
                    .order_by(SysRoleScope.role_id, SysRoleScope.seq, SysRoleScope.id)
                )
            )
            ids = {s.object_type_id for s in rows if s.object_type_id is not None}
            names = (
                dict((await self.session.execute(
                    select(ObjectType.id, ObjectType.display_name).where(ObjectType.id.in_(ids))
                )).all())
                if ids
                else {}
            )
            for s in rows:
                resolve_scope_label(s, names)
                out[s.role_id].append(s)
        return out

    async def member_counts(self, role_ids: list[int]) -> dict[int, int]:
        if not role_ids:
            return {}
        rows = await self.session.execute(
            select(SysRoleMember.role_id, func.count())
            .where(SysRoleMember.role_id.in_(role_ids))
            .group_by(SysRoleMember.role_id)
        )
        return {r: int(n) for r, n in rows.all()}

    async def name_taken(self, name: str, except_id: int | None = None) -> bool:
        stmt = select(SysRole.id).where(SysRole.name == name)
        if except_id is not None:
            stmt = stmt.where(SysRole.id != except_id)
        return (await self.session.scalar(stmt)) is not None

    async def _next_num(self, col: Any, prefix: str) -> int:
        """编号数字部分的最大值 + 1（R-01 / S-31；表很小，取出来算）。先按表加锁，并发新建不撞号。"""
        await lock_code_seq(self.session, col.table.name)
        codes = (await self.session.scalars(select(col).where(col.op("~")(rf"^{prefix}[0-9]+$")))).all()
        return max((int(c[len(prefix) :]) for c in codes), default=0) + 1

    async def next_role_code(self) -> str:
        return f"R-{await self._next_num(SysRole.code, 'R-'):02d}"

    async def next_scope_code(self) -> str:
        return f"S-{await self._next_num(SysRoleScope.code, 'S-')}"

    def members_stmt(self, role_id: int, kw: str | None):
        stmt = (
            select(SysUser)
            .join(SysRoleMember, SysRoleMember.user_id == SysUser.id)
            .where(SysRoleMember.role_id == role_id)
        )
        if kw:
            stmt = stmt.where(
                or_(
                    func.strpos(SysUser.name, kw) > 0,
                    func.strpos(func.coalesce(SysUser.account, ""), kw) > 0,
                    func.strpos(SysUser.code, kw) > 0,
                )
            )
        return stmt.order_by(SysRoleMember.seq, SysRoleMember.id)

    async def member_user_ids(self, role_id: int) -> set[int]:
        rows = await self.session.scalars(
            select(SysRoleMember.user_id).where(SysRoleMember.role_id == role_id)
        )
        return set(rows.all())

    async def min_member_seq(self, role_id: int) -> int:
        v = await self.session.scalar(
            select(func.min(SysRoleMember.seq)).where(SysRoleMember.role_id == role_id)
        )
        return int(v) if v is not None else 0

    async def roles_of_user(self, user_id: int) -> list[SysRole]:
        rows = await self.session.scalars(
            select(SysRole)
            .join(SysRoleMember, SysRoleMember.role_id == SysRole.id)
            .where(SysRoleMember.user_id == user_id)
            .order_by(SysRole.code)
        )
        return list(rows.all())


# ---- 业务逻辑 ----


class BuiltInRoleReadonly(Forbidden):
    code, message = "BUILT_IN_ROLE", "内置角色不能修改，可复制后再改"


class RoleInUse(AppError):
    status_code, code, message = 409, "ROLE_IN_USE", "有成员只有这个角色，不能删除"


class RoleValidation(AppError):
    status_code, code, message = 422, "VALIDATION_FAILED", "参数校验失败"


class RoleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = SysRoleRepository(session)

    # 读
    async def bundles(self, roles: list[SysRole]) -> list[RoleBundle]:
        ids = [r.id for r in roles]
        scopes = await self.repo.scopes_of(ids)
        counts = await self.repo.member_counts(ids)
        return [RoleBundle(r, scopes.get(r.id, []), counts.get(r.id, 0)) for r in roles]

    async def list_all(self) -> list[RoleBundle]:
        return await self.bundles(await self.repo.all_roles())

    async def object_permissions(self, object_id: str, page: PageParams):
        """对象页的只读角色投影：SQL取一页角色，不加载用户或成员明细。"""
        from app.modules.static.models import ObjectStatus, ObjectType

        obj = (
            await self.session.execute(
                select(ObjectType.code, ObjectType.display_name, ObjectType.api_name).where(
                    ObjectType.code == object_id, ObjectType.status == ObjectStatus.published
                )
            )
        ).one_or_none()
        if obj is None:
            raise NotFound("已发布对象类型不存在")
        roles, total = await self.repo.list_page(select(SysRole).order_by(SysRole.code), page)
        items = []
        for bundle in await self.bundles(list(roles)):
            matches = [p for p in bundle.role.objs if p.get("api") == obj.api_name]
            # 与实际鉴权一致：同API配置歧义时，不展示为授予权限。
            perm = matches[0] if len(matches) == 1 else None
            labels = {obj.code, obj.api_name, obj.display_name}
            if perm and perm.get("objType"):
                labels.add(perm["objType"])
            items.append(
                {
                    "id": bundle.role.code,
                    "name": bundle.role.name,
                    "defaultDeny": bundle.role.default_deny,
                    "objs": [
                        {
                            "objType": perm.get("objType", ""),
                            "api": obj.api_name,
                            "view": perm.get("view") is True,
                            "export": perm.get("export") is True,
                            "actions": perm.get("actions") or [],
                        }
                    ]
                    if perm
                    else [],
                    "scopes": [s for s in bundle.scopes if perm and scope_label(s) in labels],
                    "memberCount": bundle.member_count,
                }
            )
        return {"id": obj.code, "name": obj.display_name, "api": obj.api_name}, items, total

    async def state(self):
        """Complete edit baseline in bulk; display-only member tables use members() SQL pages."""
        roles = await self.list_all()
        users = list(
            await self.session.scalars(
                select(SysUser).where(SysUser.role.is_not(None)).order_by(SysUser.code)
            )
        )
        pairs = (
            await self.session.execute(
                select(SysRole.code, SysUser)
                .select_from(SysRole)
                .join(SysRoleMember, SysRoleMember.role_id == SysRole.id)
                .join(SysUser, SysUser.id == SysRoleMember.user_id)
                .order_by(SysRole.code, SysRoleMember.seq, SysRoleMember.id)
            )
        ).all()
        members = {r.role.code: [] for r in roles}
        for code, user in pairs:
            members[code].append(user)
        return roles, members, users

    async def _get(self, code: str) -> SysRole:
        return await self.repo.get_by_code_or_404(code)

    async def get(self, code: str) -> RoleBundle:
        return (await self.bundles([await self._get(code)]))[0]

    async def _editable(self, code: str) -> SysRole:
        r = await self._get(code)
        if r.built_in:
            raise BuiltInRoleReadonly(details={"id": code})
        return r

    async def _audit(self, user: CurrentUser, ip: str, action: str, target: str) -> None:
        await insert_audit(
            self.session,
            actor=f"{user.name}（{user.id}）",
            action=action,
            target=target,
            ip=ip,
            created_by=user.id,
        )

    def _touch(self, r: SysRole, user: CurrentUser) -> None:
        r.changed_at = now()
        r.changed_by = user.name

    # 写：角色本身
    async def create(
        self, *, name: str, desc: str, perms: list[str], copy_from: str | None, user: CurrentUser, ip: str
    ) -> RoleBundle:
        name = name.strip()
        if await self.repo.name_taken(name):
            raise DuplicateKey("角色名已存在", details={"name": name})
        src_scopes: list[SysRoleScope] = []
        if copy_from:
            src = await self._get(copy_from)
            fn, objs, deny = dict(src.func), json.loads(json.dumps(src.objs)), src.default_deny
            src_scopes = (await self.repo.scopes_of([src.id]))[src.id]
        else:
            for p in perms:
                if p not in NR_PERMS:
                    raise RoleValidation(f"未知的权限模板：{p}", details={"perm": p, "allowed": NR_PERMS})
            (fn, objs), deny = template_perms(perms, await load_perm_objects(self.session)), False
        r = SysRole(
            code=await self.repo.next_role_code(),
            name=name,
            description=desc,
            built_in=False,
            default_deny=deny,
            func=fn,
            objs=objs,
            changed_at=now(),
            changed_by=user.name,
        )
        await self.repo.create(r, actor=user.id)
        for i, s in enumerate(src_scopes):
            self.session.add(
                SysRoleScope(
                    code=await self._new_scope_code(),
                    role_id=r.id,
                    seq=i,
                    obj_type=s.obj_type,
                    object_type_id=s.object_type_id,
                    object_type_code=s.object_type_code,
                    prop=s.prop,
                    op=s.op,
                    values=list(s.values),
                    created_by=user.id,
                )
            )
            await self.session.flush()
        await self._audit(user, ip, "复制角色" if copy_from else "新建角色", f"角色「{name}」")
        return await self.get(r.code)

    async def _new_scope_code(self) -> str:
        return await self.repo.next_scope_code()

    async def update(
        self, code: str, *, name: str, desc: str, lock_version: int | None, user: CurrentUser, ip: str
    ) -> RoleBundle:
        r = await self._editable(code)
        name = name.strip()
        if await self.repo.name_taken(name, except_id=r.id):
            raise DuplicateKey("角色名已存在", details={"name": name})
        old = r.name
        self._touch(r, user)
        await self.repo.update(
            r, {"name": name, "description": desc}, actor=user.id, lock_version=lock_version
        )
        if old != name:
            # 用户页显示的主角色是角色名字符串（sys_user.role），改名时同步
            await self.session.execute(update(SysUser).where(SysUser.role == old).values(role=name))
        await self._audit(user, ip, "编辑角色", f"角色「{name}」")
        return await self.get(code)

    async def set_permissions(
        self,
        code: str,
        *,
        fn: dict[str, str],
        objs: list[dict[str, Any]],
        lock_version: int | None,
        user: CurrentUser,
        ip: str,
    ) -> RoleBundle:
        r = await self._editable(code)
        keys = {p["key"] for p in func_pages()}
        unknown = sorted(set(fn) - keys)
        if unknown:
            raise RoleValidation("未知的页面", details={"pages": unknown})
        full = {k: fn.get(k, "none") for k in [p["key"] for p in func_pages()]}
        po = await load_perm_objects(self.session)
        by_name = {o["name"]: o for o in po}
        out = []
        for o in objs:
            meta = by_name.get(o["objType"])
            if meta is None:
                raise RoleValidation("未知的对象类型", details={"objType": o["objType"]})
            bad = [a for a in o["actions"] if a not in meta["actions"]]
            if bad:
                raise RoleValidation(
                    "动作不属于该对象类型", details={"objType": o["objType"], "actions": bad}
                )
            # 与 Roles.vue 一致：取消查看时清空导出和动作
            view = bool(o["view"])
            out.append(
                {
                    "objType": meta["name"],
                    "api": meta["api"],
                    "view": view,
                    "export": view and bool(o["export"]),
                    "actions": list(o["actions"]) if view else [],
                }
            )
        given = {o["objType"] for o in out}
        for m in po:  # 没传的对象类型保留原值
            if m["name"] not in given:
                out.append(
                    next((x for x in r.objs if x["objType"] == m["name"]), None)
                    or {"objType": m["name"], "api": m["api"], "view": False, "export": False, "actions": []}
                )
        order = {m["name"]: i for i, m in enumerate(po)}
        out.sort(key=lambda x: order[x["objType"]])
        self._touch(r, user)
        await self.repo.update(r, {"func": full, "objs": out}, actor=user.id, lock_version=lock_version)
        await self._audit(user, ip, "修改角色权限", f"角色「{r.name}」")
        return await self.get(code)

    async def delete(self, code: str, user: CurrentUser, ip: str) -> None:
        """删除角色（Roles.vue deleteRole 同一套规则）。成员、范围级联删除。

        - 有成员也允许删：成员失去这个角色，其他角色不受影响；
        - 但有人「只有这一个角色」时 409 ROLE_IN_USE（details.count / details.users），删了他就没有角色了；
        - 用户页显示的主角色（sys_user.role）是这个角色名的，改成他剩下角色里编号最小的一个。
        """
        r = await self._editable(code)
        name = r.name
        member_ids = await self.repo.member_user_ids(r.id)
        primary = set((await self.session.scalars(select(SysUser.id).where(SysUser.role == name))).all())
        affected = member_ids | primary
        others: dict[int, str] = {}
        if affected:
            rows = await self.session.execute(
                select(SysRoleMember.user_id, SysRole.name)
                .join(SysRole, SysRole.id == SysRoleMember.role_id)
                .where(SysRoleMember.user_id.in_(affected), SysRole.id != r.id)
                .order_by(SysRole.code)
            )
            for uid, rname in rows.all():
                others.setdefault(uid, rname)
        orphan_ids = sorted(affected - set(others))
        if orphan_ids:
            orphans = (
                await self.session.execute(
                    select(SysUser.code, SysUser.name)
                    .where(SysUser.id.in_(orphan_ids))
                    .order_by(SysUser.code)
                )
            ).all()
            raise RoleInUse(
                f"有 {len(orphans)} 名成员只有这个角色，请先给他们分配其他角色或移出成员后再删除",
                details={"count": len(orphans), "users": [{"id": c, "name": n} for c, n in orphans]},
            )
        for uid in sorted(primary):
            await self.session.execute(update(SysUser).where(SysUser.id == uid).values(role=others[uid]))
        await self.session.delete(r)
        await self.session.flush()
        await self._audit(user, ip, "删除角色", f"角色「{name}」")

    # 写：行级范围
    async def add_scope(
        self,
        code: str,
        *,
        obj_type: str,
        prop: str,
        op: str | None,
        values: list[str],
        user: CurrentUser,
        ip: str,
        object_type_code: str | None = None,
    ) -> SysRoleScope:
        from app.modules.static.models import ObjectStatus, ObjectType

        r = await self._editable(code)
        # 当前本体里的已发布对象类型：按编号（优先）或显示名唯一定位，写入稳定引用。
        candidates = list(
            await self.session.execute(
                select(ObjectType.id, ObjectType.code, ObjectType.display_name).where(
                    ObjectType.status == ObjectStatus.published,
                    ObjectType.code == object_type_code
                    if object_type_code
                    else ObjectType.display_name == obj_type,
                )
            )
        )
        if object_type_code and len(candidates) != 1:
            raise RoleValidation("对象类型不在当前本体工作空间", details={"objectTypeCode": object_type_code})
        target = candidates[0] if len(candidates) == 1 else None
        if target is not None:
            obj_type = target.display_name
        props = await load_scope_props(self.session, user)
        meta = next((p for p in props if p["objType"] == obj_type and p["prop"] == prop), None)
        if meta is None:
            raise RoleValidation("属性不是权限依据", details={"objType": obj_type, "prop": prop})
        vals = list(dict.fromkeys(values))
        bad = [v for v in vals if v not in meta["values"]]
        if bad:
            raise RoleValidation("取值不在可选范围内", details={"values": bad})
        op = op or ("∈" if len(vals) > 1 else "=")
        if op == "=" and len(vals) != 1:
            raise RoleValidation("「=」只能选一个取值", details={"values": vals})
        seq = len((await self.repo.scopes_of([r.id]))[r.id])
        s = SysRoleScope(
            code=await self._new_scope_code(),
            role_id=r.id,
            seq=seq,
            obj_type=obj_type,
            object_type_id=target.id if target else None,
            object_type_code=target.code if target else None,
            prop=prop,
            op=op,
            values=vals,
            created_by=user.id,
        )
        self.session.add(s)
        await self.session.flush()
        self._touch(r, user)
        await self.session.flush()
        await self._audit(user, ip, "添加行级范围", f"角色「{r.name}」{obj_type}：{scope_text(s)}")
        return s

    async def remove_scope(self, code: str, scope_code: str, user: CurrentUser, ip: str) -> None:
        r = await self._editable(code)
        s = await self.session.scalar(
            select(SysRoleScope).where(SysRoleScope.role_id == r.id, SysRoleScope.code == scope_code)
        )
        if s is None:
            raise NotFound("行级范围不存在", details={"id": scope_code})
        text = f"角色「{r.name}」{s.obj_type}：{scope_text(s)}"
        await self.session.delete(s)
        self._touch(r, user)
        await self.session.flush()
        await self._audit(user, ip, "删除行级范围", text)

    # 成员
    async def members(self, code: str, kw: str | None, page: PageParams) -> tuple[list[SysUser], int]:
        r = await self._get(code)
        rows, total = await self.repo.list_page(self.repo.members_stmt(r.id, kw or None), page)  # type: ignore[arg-type]
        return list(rows), total

    async def add_members(self, code: str, user_ids: list[str], user: CurrentUser, ip: str) -> int:
        """新成员排在最前面（Roles.vue addMembers：[...add, ...members]），按传入顺序；已是成员的跳过。"""
        r = await self._get(code)
        codes = list(dict.fromkeys(user_ids))
        users = {
            u.code: u
            for u in (
                await self.session.scalars(
                    select(SysUser)
                    .where(SysUser.code.in_(codes), SysUser.role.is_not(None))
                    .order_by(SysUser.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
        }
        missing = [c for c in codes if c not in users]
        if missing:
            raise NotFound("用户不存在", details={"ids": missing})
        have = await self.repo.member_user_ids(r.id)
        inactive = [c for c in codes if users[c].id not in have and users[c].status != UserStatus.active]
        if inactive:
            raise AppError(
                "只能添加启用用户", code="VALIDATION_FAILED", status_code=422, details={"ids": inactive}
            )
        add = [users[c] for c in codes if users[c].id not in have]
        start = await self.repo.min_member_seq(r.id) - len(add)
        for i, u in enumerate(add):
            self.session.add(SysRoleMember(role_id=r.id, user_id=u.id, seq=start + i, created_by=user.id))
        await self.session.flush()
        if add:
            await self._audit(user, ip, "添加角色成员", f"角色「{r.name}」：{'、'.join(u.name for u in add)}")
        return len(add)

    async def remove_member(self, code: str, user_code: str, user: CurrentUser, ip: str) -> None:
        r = await self._get(code)
        u = await self.session.scalar(select(SysUser).where(SysUser.code == user_code))
        m = (
            await self.session.scalar(
                select(SysRoleMember).where(SysRoleMember.role_id == r.id, SysRoleMember.user_id == u.id)
            )
            if u
            else None
        )
        if m is None:
            raise NotFound("该用户不是这个角色的成员", details={"id": user_code})
        await self.session.delete(m)
        await self.session.flush()
        await self._audit(user, ip, "移出角色成员", f"角色「{r.name}」：{u.name}（{u.code}）")

    # 有效权限
    async def effective_access(self, user_code: str) -> tuple[list[RoleBundle], dict[str, Any]]:
        u = await self.session.scalar(select(SysUser).where(SysUser.code == user_code))
        if u is None:
            raise NotFound("用户不存在", details={"id": user_code})
        rs = await self.bundles(await self.repo.roles_of_user(u.id))
        objects = await load_perm_objects(self.session)
        effective = compute_effective(rs, objects)
        effective["objs"] = compute_effective(await self._bound_bundles(rs), objects)["objs"]
        return rs, effective

    async def row_scope(self, user_code: str) -> list[dict[str, Any]]:
        u = await self.session.scalar(select(SysUser).where(SysUser.code == user_code))
        if u is None:  # 服务账号等不在 sys_user 的身份：没有角色 → 全部不可见
            return compute_row_scope([], await load_perm_objects(self.session))
        rs = await self.bundles(await self.repo.roles_of_user(u.id))
        return compute_row_scope(await self._bound_bundles(rs), await load_perm_objects(self.session))


    async def _bound_bundles(self, bundles: list[RoleBundle]) -> list[RoleBundle]:
        from app.modules.ontology.models import OntologyRoleBinding

        ids = set(await self.session.scalars(select(OntologyRoleBinding.role_id).where(
            OntologyRoleBinding.ontology_id == require_ontology_id(self.session))))
        return [bundle for bundle in bundles if bundle.role.id in ids]


def compute_effective(rs: list[RoleBundle], po: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """逐行照 mock/roles.ts effectiveAccess()。"""

    def obj_of(b: RoleBundle, name: str) -> dict[str, Any] | None:
        return next((x for x in b.role.objs if x["objType"] == name), None)

    fn = []
    for p in func_pages():
        lv = "none"
        for b in rs:
            v = b.role.func.get(p["key"], "none")
            if _RANK[v] > _RANK[lv]:
                lv = v
        if lv != "none":
            fn.append({**p, "lv": lv})
    objs = []
    for o in po if po is not None else perm_objects():
        name = o["name"]

        def has_scope(b: RoleBundle, name: str = name) -> bool:
            return any(scope_label(s) == name for s in b.scopes)

        granted = [
            b
            for b in rs
            if (obj_of(b, name) or {}).get("view") and not (b.role.default_deny and not has_scope(b))
        ]
        if not granted:
            continue
        unlimited = any(not has_scope(b) for b in granted)
        scopes = (
            [] if unlimited else [scope_text(s) for b in granted for s in b.scopes if scope_label(s) == name]
        )
        actions = list(dict.fromkeys(a for b in rs for a in (obj_of(b, name) or {}).get("actions", [])))
        exp = any((obj_of(b, name) or {}).get("export") for b in rs)
        objs.append(
            {
                "name": name,
                "scope": "全部" if unlimited else "；或 ".join(dict.fromkeys(scopes)),
                "actions": actions,
                "exp": exp,
            }
        )
    return {"func": fn, "objs": objs, "areas_in": list(dict.fromkeys(p["area"] for p in fn))}


async def sync_primary_role(
    session: AsyncSession, user_id: int, previous: str | None, role: SysRole, actor: str
) -> None:
    """Replace only the explicitly changed primary role; separately assigned roles stay intact."""
    if previous == role.name:
        return
    members = list(await session.execute(
        select(SysRoleMember, SysRole.name)
        .join(SysRole, SysRole.id == SysRoleMember.role_id)
        .where(SysRoleMember.user_id == user_id)
    ))
    for member, name in members:
        if name == previous and member.role_id != role.id:
            await session.delete(member)
    if not any(member.role_id == role.id for member, _ in members):
        first = await session.scalar(
            select(func.min(SysRoleMember.seq)).where(SysRoleMember.role_id == role.id)
        )
        session.add(SysRoleMember(role_id=role.id, user_id=user_id, seq=(first or 0) - 1, created_by=actor))


async def role_names(session: AsyncSession) -> list[str]:
    """用户页的角色下拉 / 校验：sys_role 的角色名（按编号排序）。"""
    return list((await session.scalars(select(SysRole.name).order_by(SysRole.code))).all())


def compute_row_scope(rs: list[RoleBundle], po: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """当前用户逐对象类型的行级范围（问数「范围」、对象检索读它）。

    规则与 compute_effective 的 objs 一致：
    - 有查看权、且不是「defaultDeny 又没配范围」的角色才算授予；
    - 任一授予角色在该对象上没配范围 → unrestricted（不限）；
    - 否则 conditions = 各授予角色在该对象上的范围，条件之间是「或」。
    没有授予的对象也列出来（visible=false），前端可直接显示「无权查看」。
    """
    out = []
    for o in po if po is not None else perm_objects():
        name = o["name"]

        def view(b: RoleBundle, name: str = name) -> bool:
            return bool(next((x for x in b.role.objs if x["objType"] == name), {}).get("view"))

        def has_scope(b: RoleBundle, name: str = name) -> bool:
            return any(scope_label(s) == name for s in b.scopes)

        granted = [b for b in rs if view(b) and not (b.role.default_deny and not has_scope(b))]
        unlimited = bool(granted) and any(not has_scope(b) for b in granted)
        conds: list[dict[str, Any]] = []
        if granted and not unlimited:
            seen: set[str] = set()
            for b in granted:
                for s in b.scopes:
                    t = scope_text(s)
                    if scope_label(s) == name and t not in seen:
                        seen.add(t)
                        conds.append({"prop": s.prop, "op": s.op, "values": list(s.values), "text": t})
        text = "无权查看" if not granted else "全部" if unlimited else "；或 ".join(c["text"] for c in conds)
        out.append(
            {
                "obj_type": name,
                "visible": bool(granted),
                "unrestricted": unlimited,
                "conditions": conds,
                "text": text,
            }
        )
    return out


async def tenant_options(session: AsyncSession) -> list[dict[str, Any]]:
    """顶栏下拉：每个组织一项，label =「租户简称 / 组织名」（一期单租户，取第一行租户）。"""
    t = await session.scalar(select(SysTenant).order_by(SysTenant.id).limit(1))
    orgs = (await session.scalars(select(SysOrg).order_by(SysOrg.seq, SysOrg.code))).all()
    short = t.short_name if t else ""
    return [
        {
            "value": o.code,
            "label": f"{short} / {o.name}" if short else o.name,
            "tenant_id": t.code if t else "",
            "tenant_name": t.name if t else "",
            "org_name": o.name,
            "default": o.is_default,
        }
        for o in orgs
    ]
