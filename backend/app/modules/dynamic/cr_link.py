"""规则 / 生命周期草稿登记到变更单（static cr_write，同一事务）。

- 单号在 change_request 表里才登记；不在（mock 单号、老测试数据）静默跳过，草稿行照旧保存；
- 每个 section 整段 PUT（static 契约：一次 PUT lockVersion +1）；本次没有行且原来也没有行的 section 不 PUT，
  免得白白 +1；lockVersion 只校验第一次 PUT（后面几次是同一请求自己 +1 的）；
- 规则：target kind=rule，section conds / mapping / config；新规则 isNew，全部 op=add，config 一个字段一行；
- 生命周期：target kind=object（id = 对象类型），section lc_states / lc_transitions。
"""

from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser
from app.core.errors import NotFound, VersionConflict
from app.modules.static.cr_write import CrWriteService, text_of
from app.modules.static.models import ChangeRequest, CrItem, CrTarget
from app.modules.static.schemas import ItemIn, ItemsPutIn, TargetIn

Row = dict[str, Any]
RULE_CONFIG_NEW = ("name", "objKind", "level", "output", "logic", "trigger", "expr", "dedupe", "cooldown")
STAGE_FIELDS = (("enabled", "自动暂存"), ("actionTypeId", "暂存动作"), ("confirmer", "确认人"))


async def discard_lifecycle_draft(session: AsyncSession, cr: str, object_id: str) -> None:
    """静态放弃对象目标时，同事务删除生命周期编辑，避免重新加入后复活旧草稿。"""
    from app.modules.dynamic.rules_models import Lifecycle, LifecycleDraft

    await session.execute(
        delete(LifecycleDraft).where(
            LifecycleDraft.cr_code == cr,
            LifecycleDraft.lifecycle_id.in_(select(Lifecycle.id).where(Lifecycle.code == object_id)),
        )
    )


class CrLink:
    def __init__(self, session: AsyncSession, user: CurrentUser) -> None:
        self.session = session
        self.user = user
        self.svc = CrWriteService(session)

    async def exists(self, cr: str | None) -> bool:
        if not cr:
            return False
        return bool(await self.session.scalar(select(func.count()).where(ChangeRequest.code == cr)))

    async def registered(self, cr: str, kind: str, tid: str) -> bool:
        q = (
            select(func.count())
            .select_from(CrTarget)
            .join(ChangeRequest, ChangeRequest.id == CrTarget.cr_id)
            .where(ChangeRequest.code == cr, CrTarget.target_kind == kind, CrTarget.target_id == tid)
        )
        return bool(await self.session.scalar(q))

    async def join(self, cr: str | None, target: TargetIn) -> None:
        if await self.exists(cr):
            await self.svc.add_target(cr, self.user, target)  # type: ignore[arg-type]

    async def discard(self, cr: str | None, kind: str, tid: str) -> None:
        """放弃登记（删该目标的草稿行 + 登记）；单子不在库里 / 没登记过都忽略，CR_LOCKED 照抛。"""
        if not await self.exists(cr):
            return
        try:
            await self.svc.discard_target(cr, kind, tid, self.user)  # type: ignore[arg-type]
        except NotFound:
            return

    async def _has(self, cr: str, kind: str, tid: str, section: str) -> bool:
        q = (
            select(func.count())
            .select_from(CrItem)
            .join(ChangeRequest, ChangeRequest.id == CrItem.cr_id)
            .where(
                ChangeRequest.code == cr,
                CrItem.target_kind == kind,
                CrItem.target_id == tid,
                CrItem.section == section,
            )
        )
        return bool(await self.session.scalar(q))

    async def put(
        self, cr: str | None, target: TargetIn, sections: dict[str, list[Row]], lock_version: int | None
    ) -> Row | None:
        """整段登记若干 section；返回 {lockVersion, changes}（单子不在库里返回 None）。"""
        if not await self.exists(cr):
            return None
        assert cr
        lv = lock_version
        put = False
        for section, rows in sections.items():
            if not rows and not await self._has(cr, target.kind, target.id, section):
                continue
            body = ItemsPutIn(
                target=target, section=section, rows=[ItemIn(**r) for r in rows], lock_version=lv
            )
            await self.svc.put_items(cr, self.user, body)
            lv, put = None, True
        if not put:  # 什么都没 PUT：至少登记目标（幂等），并校验调用方的 lockVersion
            await self.svc.add_target(cr, self.user, target)
        row = await self.session.scalar(select(ChangeRequest).where(ChangeRequest.code == cr))
        assert row is not None
        if not put and lock_version is not None and lock_version != row.lock_version:
            raise VersionConflict(details={"id": cr, "lockVersion": row.lock_version})
        return {"lockVersion": row.lock_version, "changes": row.changes}


def _row(key: str, op: str, before: Any, after: Any, typ: str, field: str) -> Row:
    return {
        "key": key,
        "op": op,
        "before": None if op == "add" else before,
        "after": None if op == "delete" else after,
        "type": typ,
        "field": field,
        "before_text": "" if op == "add" else text_of(before),
        "after_text": "" if op == "delete" else text_of(after),
    }


def cond_text(c: dict[str, Any]) -> str:
    return f"{c.get('field') or c.get('metric') or ''} {c.get('op') or ''} {c.get('value') or ''}".strip()


def rule_sections(pub: Row | None, cur: Row) -> dict[str, list[Row]]:
    """规则草稿 vs 已发布定义 → conds / mapping / config 三段行；pub=None 表示新规则（全部 add）。"""
    new = pub is None
    conds: list[Row] = []
    for i, c in enumerate(cur.get("conds") or []):
        pc = (pub or {}).get("conds") or []
        old = pc[i] if i < len(pc) else None
        if new or old is None:
            conds.append(
                {
                    **_row(f"cond{i + 1}", "add", None, c, "规则条件", c.get("field") or ""),
                    "after_text": cond_text(c),
                }
            )
        elif old != c:
            r = _row(f"cond{i + 1}", "update", old, c, "规则条件", c.get("field") or "")
            conds.append({**r, "before_text": cond_text(old), "after_text": cond_text(c)})
    for i in range(len(cur.get("conds") or []), len((pub or {}).get("conds") or [])):
        old = pub["conds"][i]
        conds.append(_row(f"cond{i + 1}", "delete", old, None, "规则条件", old.get("field") or ""))
    st_cur = cur.get("autoStage") or {}
    st_pub = {} if new else ((pub or {}).get("autoStage") or {})
    mp_cur = {m["param"]: m.get("from") for m in st_cur.get("mapping") or []}
    mp_pub = {m["param"]: m.get("from") for m in st_pub.get("mapping") or []}
    mapping: list[Row] = []
    for p, f in mp_cur.items():
        if p not in mp_pub:
            mapping.append(_row(f"map.{p}", "add", None, f, "参数映射", p))
        elif mp_pub[p] != f:
            mapping.append(_row(f"map.{p}", "update", mp_pub[p], f, "参数映射", p))
    for p, f in mp_pub.items():
        if p not in mp_cur:
            mapping.append(_row(f"map.{p}", "delete", f, None, "参数映射", p))
    config: list[Row] = []
    if new:
        for k in RULE_CONFIG_NEW:
            config.append(_row(k, "add", None, cur.get(k), "规则配置", k))
    else:
        for k in RULE_CONFIG_NEW:
            if (pub or {}).get(k) != cur.get(k):
                config.append(_row(k, "update", pub.get(k), cur.get(k), "规则配置", k))
    for k, label in STAGE_FIELDS:
        a, b = st_pub.get(k), st_cur.get(k)
        if new:
            if b not in (None, "", False):
                config.append(_row(f"autoStage.{k}", "add", None, b, "自动暂存", label))
        elif a != b:
            config.append(_row(f"autoStage.{k}", "update", a, b, "自动暂存", label))
    return {"conds": conds, "mapping": mapping, "config": config}


def lifecycle_sections(cur: Row, edits: Row) -> dict[str, list[Row]]:
    """生命周期草稿改动 → lc_states / lc_transitions 两段行。"""
    states = [
        _row(f"state.{s.get('key') or s.get('name')}", "add", None, s, "状态", str(s.get("name") or ""))
        for s in edits.get("addStates") or []
    ]
    by_id = {t["id"]: t for t in cur.get("transitions") or []}
    tr_name = lambda t: f"{t.get('from')} → {t.get('to')}"  # noqa: E731
    trs: list[Row] = []
    for t in edits.get("addTransitions") or []:
        trs.append(_row(f"tr.{t.get('id') or tr_name(t)}", "add", None, t, "迁移", tr_name(t)))
    for k, e in (edits.get("editTransitions") or {}).items():
        old = by_id.get(k)
        if old is not None:
            trs.append(_row(f"tr.{k}", "update", old, {**old, **e}, "迁移", tr_name(old)))
    for k in edits.get("removeTransitions") or []:
        old = by_id.get(k)
        if old is not None:
            trs.append(_row(f"tr.{k}", "delete", old, None, "迁移", tr_name(old)))
    return {"lc_states": states, "lc_transitions": trs}
