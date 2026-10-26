"""变更单写接口：发起 / 加入 / 放弃目标 / 保存草稿行 / 读回 / 校验 / 改动列表 / 改单头。

契约见 backend/docs/backend/handoff/static.md「变更单写接口契约」。目标通用（kind + 业务编码），
动态（规则 / 生命周期）和 Palantir（动作 / 事件类型）的草稿也走这里，后台不校验目标是否存在。

[推断] 汇总：
- 一个定义同时只能在一张「未发布」的单子里（草稿 / 审核中 / 已通过 / 已驳回都算占用；mock DRAFT_TARGETS
  只登记草稿，前端 draftOf 也只查草稿，后台从严）→ 409 TARGET_IN_OTHER_CR。
- changes = 原有改动数（种子单子 = mock changes）− 被替换掉的 cr_item 行数 + 新的行数（种子改动数不动）。
- 默认审核人 = 本体管理员（demo_admin_codes 第一个的姓名）；发起人就是他时留空，提交时 409 CR_NO_REVIEWER。
- :validate 同步计算（设计稿写的异步 jobId 不做）；种子单子取「种子结果、计算结果」里更严重的一档。
"""

import json
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.common.auth import CurrentUser
from app.core import clock
from app.core.config import get_settings
from app.core.errors import AppError, NotFound, VersionConflict
from app.core.schema import _fmt_dt
from app.modules.static.models import ChangeRequest, CrCheck, CrEvent, CrItem, CRLevel, CRStatus, CrTarget
from app.modules.static.schemas import CRCreateIn, CRPatchIn, ItemsPutIn, TargetIn

Row = dict[str, Any]
S = CRStatus

KIND_LABEL = {
    "object": "对象类型",
    "link": "关系",
    "metric": "指标",
    "dimension": "维度",
    "knowledge": "业务知识",
    "action": "动作类型",
    "event": "事件类型",
    "rule": "规则",
}
OP_LABEL = {"add": "新增", "update": "修改", "delete": "删除"}
# mock/changes.ts CHECK_NAMES
CHECK_NAMES = [
    ("V1", "主键与映射完整"),
    ("V2", "主键唯一性抽检"),
    ("V3", "关系两端与类型兼容"),
    ("V4", "枚举挂取值字典"),
    ("V5", "指标引用的属性已映射"),
    ("V6", "允许的维度可到达"),
    ("V7", "已发布 API 名不删不改"),
    ("V8", "改公式或过滤须升版本"),
    ("V9", "同义词冲突"),
    ("V10", "层级成员唯一父级"),
    ("V11", "自然语言描述齐全"),
    ("V12", "影响分析"),
    ("V13", "多路径须指定默认路径"),
    ("V14", "各目标库 EXPLAIN 通过"),
    ("V15", "重大改动写明迁移方式"),
    ("V16", "冲突策略时间字段"),
    ("V17", "同名词唯一默认"),
]
RESULT_CODE = {"阻断": "block", "警告": "warn", "信息": "info", "通过": "pass", "不适用": "na"}
SEVERITY = {"阻断": 4, "警告": 3, "信息": 2, "通过": 1, "不适用": 0}
API_SECTIONS = {"properties", "links"}  # V7：已发布 API 名所在的段
DESC_KEYS = ("desc", "definition", "description")
HOLDING = (S.draft, S.in_review, S.approved, S.rejected)


def text_of(v: Any) -> str:
    """before / after 转改动列表显示文字。"""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, str | int | float):
        return str(v)
    if isinstance(v, list):
        return "、".join(text_of(x) for x in v)
    if isinstance(v, dict):
        for k in ("label", "name", "value", "text"):
            if isinstance(v.get(k), str | int | float):
                return str(v[k])
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def _locked(cr: ChangeRequest) -> None:
    if cr.status != S.draft:
        raise AppError(
            f"{cr.code} 当前是「{cr.status.value}」，草稿才能修改",
            code="CR_LOCKED",
            status_code=409,
            details={"id": cr.code, "status": cr.status.value},
        )


def _check_version(cr: ChangeRequest, lock_version: int | None) -> None:
    if lock_version is not None and lock_version != cr.lock_version:
        raise VersionConflict(details={"id": cr.code, "lockVersion": cr.lock_version})


def item_out(i: CrItem) -> Row:
    return {
        "target": {"kind": i.target_kind, "id": i.target_id},
        "section": i.section,
        "key": i.item_key,
        "op": i.op,
        "before": i.before,
        "after": i.after,
        "type": i.type_label,
        "field": i.field_label,
        "updatedBy": i.updated_by,
        "updatedAt": _fmt_dt(i.updated_at),
    }


def target_out(t: CrTarget) -> Row:
    r: Row = {"kind": t.target_kind, "id": t.target_id, "isNew": t.is_new}
    if t.target_name:
        r["name"] = t.target_name
    return r


def diff_row(i: CrItem, names: dict[tuple[str, str], str]) -> Row:
    return {
        "key": f"i{i.id}",
        "type": i.type_label or KIND_LABEL.get(i.target_kind, i.target_kind),
        "target": names.get((i.target_kind, i.target_id)) or i.target_id,
        "field": i.field_label or i.item_key,
        "kind": OP_LABEL[i.op],
        "before": i.before_text if i.before_text is not None else text_of(i.before),
        "after": i.after_text if i.after_text is not None else text_of(i.after),
    }


class CrWriteService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------ 公共
    async def _cr(self, code: str, *, lock: bool = True) -> ChangeRequest:
        q = select(ChangeRequest).where(ChangeRequest.code == code)
        cr = await self.session.scalar(q.with_for_update() if lock else q)
        if cr is None:
            raise NotFound("变更单不存在", details={"id": code})
        return cr

    async def targets(self, cr_id: int) -> list[CrTarget]:
        q = select(CrTarget).where(CrTarget.cr_id == cr_id).order_by(CrTarget.seq, CrTarget.id)
        return list(await self.session.scalars(q))

    async def items(self, cr_id: int) -> list[CrItem]:
        q = select(CrItem).where(CrItem.cr_id == cr_id).order_by(CrItem.seq, CrItem.id)
        return list(await self.session.scalars(q))

    async def item_diffs(self, cr_id: int) -> list[Row]:
        items = await self.items(cr_id)
        if not items:
            return []
        names = {(t.target_kind, t.target_id): t.target_name or "" for t in await self.targets(cr_id)}
        return [diff_row(i, names) for i in items]

    def _touch(self, cr: ChangeRequest) -> None:
        """detail 有改动就标脏：ORM 每次 UPDATE 都把 lock_version +1（version_id_col）。"""
        cr.status_at = clock.now()
        flag_modified(cr, "detail")

    async def _holder(self, kind: str, tid: str, cr_id: int) -> str | None:
        q = (
            select(ChangeRequest.code)
            .join(CrTarget, CrTarget.cr_id == ChangeRequest.id)
            .where(
                CrTarget.target_kind == kind,
                CrTarget.target_id == tid,
                ChangeRequest.id != cr_id,
                ChangeRequest.status.in_(HOLDING),
            )
        )
        return await self.session.scalar(q.limit(1))

    async def _join(self, cr: ChangeRequest, t: TargetIn, user: CurrentUser) -> CrTarget:
        hit = await self.session.scalar(
            select(CrTarget).where(
                CrTarget.cr_id == cr.id, CrTarget.target_kind == t.kind, CrTarget.target_id == t.id
            )
        )
        if hit is not None:
            if t.name and not hit.target_name:
                hit.target_name = t.name
            return hit
        if other := await self._holder(t.kind, t.id, cr.id):
            raise AppError(
                f"{t.id} 已在变更单 {other} 里，请加入那张单子",
                code="TARGET_IN_OTHER_CR",
                status_code=409,
                details={"crId": other, "kind": t.kind, "id": t.id},
            )
        n = await self.session.scalar(select(func.count()).where(CrTarget.cr_id == cr.id)) or 0
        row = CrTarget(
            cr_id=cr.id,
            target_kind=t.kind,
            target_id=t.id,
            target_name=t.name,
            is_new=t.is_new,
            seq=n,
            created_by=user.id,
        )
        self.session.add(row)
        d = dict(cr.detail or {})
        touches = list(d.get("touches") or [])
        if f"{t.kind}:{t.id}" not in touches:
            touches.append(f"{t.kind}:{t.id}")
        d["touches"] = touches
        d.setdefault("edit", {"kind": t.kind, "id": t.id})
        cr.detail = d
        self._touch(cr)
        await self.session.flush()
        return row

    def _event(self, cr: ChangeRequest, user: CurrentUser | None, action: str, text: str) -> None:
        self.session.add(
            CrEvent(
                cr_id=cr.id,
                action=action,
                actor=user.name if user else "系统",
                actor_code=user.id if user else None,
                created_by=user.id if user else None,
                text=f"{user.name} {text}" if user else f"系统 {text}",
                at=clock.now(),
            )
        )

    async def _default_reviewer(self, owner: str) -> str:
        from app.modules.admin.models import SysUser

        codes = get_settings().demo_admin_codes
        if not codes:
            return ""
        name = await self.session.scalar(select(SysUser.name).where(SysUser.code == codes[0]))
        return "" if not name or name == owner else name

    async def _next_code(self) -> str:
        codes = await self.session.scalars(select(ChangeRequest.code).where(ChangeRequest.code.like("CR-%")))
        n = max((int(c[3:]) for c in codes if c[3:].isdigit()), default=0)
        return f"CR-{n + 1:04d}"

    # ------------------------------------------------------------ 发起 / 目标
    async def create(self, user: CurrentUser, body: CRCreateIn) -> str:
        now = clock.now()
        if body.target is not None and (other := await self._holder(body.target.kind, body.target.id, -1)):
            raise AppError(
                f"{body.target.id} 已在变更单 {other} 里，请加入那张单子",
                code="TARGET_IN_OTHER_CR",
                status_code=409,
                details={"crId": other, "kind": body.target.kind, "id": body.target.id},
            )
        reviewer = body.reviewer if body.reviewer is not None else await self._default_reviewer(user.name)
        cr = ChangeRequest(
            code=await self._next_code(),
            title=body.title.strip(),
            reason=body.reason,
            owner=user.name,
            reviewer=reviewer,
            status=S.draft,
            level=CRLevel(body.level),
            changes=0,
            detail={
                "diffs": [],
                "checks": [],
                "impact": {"direct": [], "defs": [], "usage": []},
                "touches": [],
                "times": {"created": _fmt_dt(now)},
            },
            status_at=now,
            created_by=user.id,
        )
        self.session.add(cr)
        await self.session.flush()
        self._event(cr, user, "create", "创建变更单")
        if body.target is not None:
            await self._join(cr, body.target, user)
        await self.session.flush()
        return cr.code

    async def add_target(self, code: str, user: CurrentUser, t: TargetIn) -> str:
        cr = await self._cr(code)
        if cr.status != S.draft:
            raise AppError(
                f"{cr.code} 当前是「{cr.status.value}」，草稿才能加入",
                code="CR_STATE",
                status_code=409,
                details={"id": cr.code, "status": cr.status.value},
            )
        await self._join(cr, t, user)
        return cr.code

    async def discard_target(self, code: str, kind: str, tid: str) -> None:
        cr = await self._cr(code)
        hit = await self.session.scalar(
            select(CrTarget).where(
                CrTarget.cr_id == cr.id, CrTarget.target_kind == kind, CrTarget.target_id == tid
            )
        )
        if hit is None:
            raise NotFound("目标不在这张变更单里", details={"id": code, "kind": kind, "targetId": tid})
        _locked(cr)
        n = (
            await self.session.execute(
                delete(CrItem).where(
                    CrItem.cr_id == cr.id, CrItem.target_kind == kind, CrItem.target_id == tid
                )
            )
        ).rowcount
        await self.session.delete(hit)
        cr.changes = max(0, cr.changes - (n or 0))
        d = dict(cr.detail or {})
        d["touches"] = [x for x in d.get("touches") or [] if x != f"{kind}:{tid}"]
        if d.get("edit") == {"kind": kind, "id": tid}:
            rest = [t for t in await self.targets(cr.id) if t.id != hit.id]
            if rest:
                d["edit"] = {"kind": rest[0].target_kind, "id": rest[0].target_id}
            else:
                d.pop("edit", None)
        cr.detail = d
        self._touch(cr)
        await self.session.flush()

    # ------------------------------------------------------------ 草稿行
    async def put_items(self, code: str, user: CurrentUser, body: ItemsPutIn) -> Row:
        cr = await self._cr(code)
        _locked(cr)
        _check_version(cr, body.lock_version)
        keys = [r.key for r in body.rows]
        if dup := sorted({k for k in keys if keys.count(k) > 1}):
            raise AppError(
                "同一段里行键重复",
                code="VALIDATION_FAILED",
                status_code=422,
                details={"fields": [{"field": "rows", "message": f"key 重复：{'、'.join(dup)}"}]},
            )
        t = body.target
        await self._join(cr, t, user)
        where = (
            CrItem.cr_id == cr.id,
            CrItem.target_kind == t.kind,
            CrItem.target_id == t.id,
            CrItem.section == body.section,
        )
        old = (await self.session.execute(delete(CrItem).where(*where))).rowcount or 0
        base = await self.session.scalar(
            select(func.coalesce(func.max(CrItem.seq), 0)).where(CrItem.cr_id == cr.id)
        )
        for n, r in enumerate(body.rows, start=1):
            self.session.add(
                CrItem(
                    cr_id=cr.id,
                    target_kind=t.kind,
                    target_id=t.id,
                    section=body.section,
                    item_key=r.key,
                    op=r.op,
                    before=r.before,
                    after=None if r.op == "delete" and r.after is None else r.after,
                    type_label=r.type,
                    field_label=r.field,
                    before_text=r.before_text,
                    after_text=r.after_text,
                    updated_by=user.name,
                    created_by=user.id,
                    seq=(base or 0) + n,
                )
            )
        cr.changes = max(0, cr.changes - old) + len(body.rows)
        self._touch(cr)
        await self.session.flush()
        rows = await self.session.scalars(select(CrItem).where(*where).order_by(CrItem.seq, CrItem.id))
        return {
            "id": cr.code,
            "changes": cr.changes,
            "lockVersion": cr.lock_version,
            "items": [item_out(i) for i in rows],
        }

    async def list_items(
        self, code: str, kind: str | None, tid: str | None, section: str | None
    ) -> list[Row]:
        cr = await self._cr(code, lock=False)
        q = select(CrItem).where(CrItem.cr_id == cr.id)
        if kind:
            q = q.where(CrItem.target_kind == kind)
        if tid:
            q = q.where(CrItem.target_id == tid)
        if section:
            q = q.where(CrItem.section == section)
        return [item_out(i) for i in await self.session.scalars(q.order_by(CrItem.seq, CrItem.id))]

    async def draft_targets(self, status: CRStatus) -> dict[str, dict[str, list[str]]]:
        q = (
            select(ChangeRequest.code, CrTarget.target_kind, CrTarget.target_id)
            .join(CrTarget, CrTarget.cr_id == ChangeRequest.id)
            .where(ChangeRequest.status == status)
            .order_by(ChangeRequest.code.desc(), CrTarget.seq, CrTarget.id)
        )
        out: dict[str, dict[str, list[str]]] = {}
        for code, kind, tid in await self.session.execute(q):
            out.setdefault(code, {}).setdefault(kind, []).append(tid)
        return out

    async def patch(self, code: str, body: CRPatchIn) -> str:
        cr = await self._cr(code)
        _locked(cr)
        _check_version(cr, body.lock_version)
        if body.title is not None:
            cr.title = body.title.strip()
        if body.reason is not None:
            cr.reason = body.reason
        if body.reviewer is not None:
            cr.reviewer = body.reviewer
        if body.level is not None:
            cr.level = CRLevel(body.level)
        d = dict(cr.detail or {})
        if body.migration is not None:
            d["migration"] = body.migration
        cr.detail = d
        self._touch(cr)
        await self.session.flush()
        return cr.code

    # ------------------------------------------------------------ 校验
    async def _baseline(self, cr: ChangeRequest, now: Any) -> dict[str, Row]:
        """种子单子原有的非通过结果（mock 的 V5 / V14 阻断等）：第一次 :validate 时存成 target_db='seed' 行，
        以后每次合并它，而不是合并上一次的计算结果（否则自己算出的阻断改掉后也洗不掉）。[推断]"""
        rows = (
            await self.session.scalars(
                select(CrCheck).where(CrCheck.cr_id == cr.id, CrCheck.target_db == "seed")
            )
        ).all()
        if rows:
            label = {v: k for k, v in RESULT_CODE.items()}
            return {r.code: {"result": label[r.result], "note": r.note or ""} for r in rows}
        done = await self.session.scalar(
            select(func.count()).select_from(CrCheck).where(CrCheck.cr_id == cr.id)
        )
        if done:
            return {}
        base = {
            r["code"]: r
            for r in (cr.detail or {}).get("checks") or []
            if SEVERITY.get(r.get("result"), 0) > SEVERITY["通过"]
        }
        for c, r in base.items():
            self.session.add(
                CrCheck(
                    cr_id=cr.id,
                    code=c,
                    target_db="seed",
                    name=r.get("name") or c,
                    result=RESULT_CODE[r["result"]],
                    note=r.get("note", ""),
                    checked_at=now,
                )
            )
        return base

    async def validate(self, code: str) -> Row:
        cr = await self._cr(code)
        if cr.status == S.published:
            raise AppError(
                f"{cr.code} 已发布，不能重新校验",
                code="CR_STATE",
                status_code=409,
                details={"id": cr.code, "status": cr.status.value},
            )
        now = clock.now()
        targets = await self.targets(cr.id)
        items = await self.items(cr.id)
        is_new = {(t.target_kind, t.target_id): t.is_new for t in targets}
        kinds = {t.target_kind for t in targets}
        calc: dict[str, tuple[str, str]] = {}
        dels = [
            f"{i.target_id}.{i.item_key}"
            for i in items
            if i.section in API_SECTIONS and i.op == "delete" and not is_new.get((i.target_kind, i.target_id))
        ]
        if dels:
            calc["V7"] = ("阻断", f"已发布的 {'、'.join(dels)} 不能删除，只能弃用")
        empty = [
            f"{i.target_id}.{i.item_key}"
            for i in items
            if isinstance(i.after, dict)
            and any(k in i.after and not str(i.after[k] or "").strip() for k in DESC_KEYS)
        ]
        if empty:
            calc["V11"] = ("警告", f"{'、'.join(empty)} 还没有描述")
        if cr.level == CRLevel.major and not (cr.detail or {}).get("migration"):
            calc["V15"] = ("警告", "还没写已有页面和告警的迁移方式")
        calc["V12"] = ("通过", f"改动 {cr.changes} 处，涉及 {len(targets)} 个定义")
        if "link" not in kinds:
            calc.setdefault("V3", ("不适用", "本次未改关系"))
            calc.setdefault("V13", ("不适用", "本次未改关系"))
        if "metric" not in kinds:
            calc.setdefault("V8", ("不适用", "本次未改指标"))
        if "dimension" not in kinds:
            calc.setdefault("V4", ("不适用", "本次未改维度"))
            calc.setdefault("V10", ("不适用", "本次未改维度"))
        if "object" not in kinds:
            calc.setdefault("V16", ("不适用", ""))
        seed = await self._baseline(cr, now)
        checks: list[Row] = []
        for c, name in CHECK_NAMES:
            res, note = calc.get(c, ("通过", ""))
            s = seed.get(c)
            if s and SEVERITY.get(s.get("result"), 0) > SEVERITY[res]:
                res, note = s["result"], s.get("note", "")
            checks.append({"code": c, "name": name, "result": res, "note": note})
        await self.session.execute(delete(CrCheck).where(CrCheck.cr_id == cr.id, CrCheck.target_db == ""))
        for r in checks:
            self.session.add(
                CrCheck(
                    cr_id=cr.id,
                    code=r["code"],
                    target_db="",
                    name=r["name"],
                    result=RESULT_CODE[r["result"]],
                    note=r["note"],
                    checked_at=now,
                )
            )
        d = dict(cr.detail or {})
        d["checks"] = checks
        d["times"] = {**(d.get("times") or {}), "checked": _fmt_dt(now)}
        cr.detail = d
        self._touch(cr)
        self._event(cr, None, "validate", "完成发布校验")
        await self.session.flush()
        cnt: dict[str, int] = {}
        for r in checks:
            cnt[r["result"]] = cnt.get(r["result"], 0) + 1
        summary = " · ".join(
            f"{k} {cnt[k]}" for k in ("阻断", "警告", "信息", "通过", "不适用") if cnt.get(k)
        )
        return {
            "id": cr.code,
            "checkedAt": _fmt_dt(now),
            "checks": checks,
            "summary": summary,
            "blocked": bool(cnt.get("阻断")),
        }
