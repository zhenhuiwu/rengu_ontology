"""变更单列表 / 详情和状态流转（Releases.vue 左侧列表、详情区按钮）。

状态机照 mock（Releases.vue `move()` 及各按钮的显示条件）原样搬：
| 动作 | 从 → 到 | 谁能做（前端按钮条件） | 评审记录（{me} + text / desc / status） |
|---|---|---|---|
| submit 提交审核 | 草稿 → 审核中 | 不限提交人；有阻断或没有改动不能提交 | 提交审核 / 审核人 {reviewer} |
| withdraw 撤回提交 | 审核中 → 草稿 | owner | 撤回提交 |
| approve 审核通过 | 审核中 → 已通过 | reviewer 且不是 owner；有阻断不能通过 | 审核通过 / 等待整版发布 {v} |
| reject 驳回 | 审核中 → 已驳回 | reviewer 且不是 owner；rejectReason 必填 | 驳回 / {reason} / error |
| unapprove 撤回 | 已通过 → 草稿 | owner 或 reviewer | 撤回审核结果 / 不进入 {next} |
| reopen 改回草稿 | 已驳回 → 草稿 | owner 或 reviewer | 改回草稿 / 按驳回意见修改后重新提交 |
| delete 删除草稿 | 草稿 → （删除） | owner | — |

[推断] 错误码：状态不对 → 409 CR_STATE；有阻断 → 409 CR_BLOCKED；没有改动 → 409 CR_EMPTY；
人不对 → 403 NO_PERMISSION（骨架 Forbidden）；驳回原因为空 → 422 VALIDATION_FAILED。
[推断] 删除草稿是硬删（表没有软删除列，前端也没有恢复入口），评审记录随之级联删除。
"""

from typing import Any

from sqlalchemy import BigInteger, literal, or_, select, true, tuple_, union_all
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.common.auth import CurrentUser
from app.common.pagination import Page, PageParams
from app.core import clock
from app.core.errors import AppError, Forbidden, NotFound
from app.core.schema import _fmt_dt
from app.db.dialects import get_adapter
from app.db.types import JsonType
from app.modules.static.cr_analysis import visible_analysis
from app.modules.static.cr_write import CrWriteService, diff_row, target_out
from app.modules.static.models import ChangeRequest, CrEvent, CrItem, CRStatus, CrTarget
from app.modules.static.permissions import check_cr, cr_owner_id, is_cr_owner
from app.modules.static.repo import StaticRepo
from app.modules.versioning.models import OntologyRelease
from app.modules.versioning.service import VersioningService, next_ver

Row = dict[str, Any]
S = CRStatus


def _state(cr: ChangeRequest, *allowed: CRStatus) -> None:
    if cr.status not in allowed:
        raise AppError(
            f"{cr.code} 当前是「{cr.status.value}」，不能这样操作",
            code="CR_STATE",
            status_code=409,
            details={"id": cr.code, "status": cr.status.value},
        )


def _blocked_codes(cr: ChangeRequest) -> list[str]:
    return [r.get("code") for r in cr.detail.get("checks") or [] if r.get("result") == "阻断"]


def _raise_blocked(cr: ChangeRequest) -> None:
    """服务端重算后仍有阻断：409 CR_BLOCKED，details.blocked 带校验码（事务回滚，结果不落库）。"""
    if codes := _blocked_codes(cr):
        raise AppError(
            f"有 {len(codes)} 项阻断未通过",
            code="CR_BLOCKED",
            status_code=409,
            details={"id": cr.code, "blocked": codes},
        )


class ChangeRequestService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------ 读
    @staticmethod
    def row(cr: ChangeRequest, ver: str | None, metadata: Row | None = None) -> Row:
        """mock ChangeRequest 原样：可选键（release / rejectReason）没有就不给。"""
        r: Row = {
            "id": cr.code,
            "title": cr.title,
            "owner": cr.owner,
            "reviewer": cr.reviewer,
            "status": cr.status.value,
            "level": cr.level.value,
            "changes": cr.changes,
            "updatedAt": _fmt_dt(cr.status_at),
            "reason": cr.reason,
        }
        if ver:
            r["release"] = ver
        if owner_id := cr_owner_id(cr):
            r["ownerId"] = owner_id
        if reviewer_id := ((cr.detail if metadata is None else metadata) or {}).get("reviewerCode"):
            r["reviewerId"] = reviewer_id
        if cr.reject_reason:
            r["rejectReason"] = cr.reject_reason
        return r

    async def workflow(self, *, status: str | None = None, kw: str = "") -> list[Row]:
        """完整轻工作流目录：不读取整份detail、items或events，不受页码影响。"""
        q = (
            select(
                ChangeRequest,
                OntologyRelease.ver,
                ChangeRequest.detail["touches"],
                ChangeRequest.detail["reviewerCode"].as_string(),
            )
            .options(defer(ChangeRequest.detail, raiseload=True))
            .outerjoin(OntologyRelease, OntologyRelease.id == ChangeRequest.release_id)
            .order_by(ChangeRequest.code.desc())
        )
        if status:
            q = q.where(ChangeRequest.status == CRStatus(status))
        if kw:
            q = q.where(
                or_(
                    ChangeRequest.title.contains(kw, autoescape=True),
                    ChangeRequest.code.contains(kw.upper(), autoescape=True),
                )
            )
        records = list(await self.session.execute(q))
        ids = [record[0].id for record in records]
        targets = {key: [] for key in ids}
        if ids:
            for target in await self.session.scalars(
                select(CrTarget).where(CrTarget.cr_id.in_(ids)).order_by(CrTarget.seq, CrTarget.id)
            ):
                targets[target.cr_id].append(target_out(target))
        out = []
        for cr, ver, legacy, reviewer in records:
            touches = (
                [value for value in legacy if isinstance(value, str)] if isinstance(legacy, list) else []
            )
            touches = list(dict.fromkeys([*touches, *(f"{t['kind']}:{t['id']}" for t in targets[cr.id])]))
            out.append(
                {
                    **self.row(cr, ver, {"reviewerCode": reviewer}),
                    "detail": {"touches": touches},
                    "targets": targets[cr.id],
                }
            )
        return out

    async def _events(self, ids: list[int]) -> dict[int, list[Row]]:
        out: dict[int, list[Row]] = {i: [] for i in ids}
        if not ids:
            return out
        q = select(CrEvent).where(CrEvent.cr_id.in_(ids)).order_by(CrEvent.at.desc(), CrEvent.id.desc())
        for e in await self.session.scalars(q):
            ev: Row = {"time": _fmt_dt(e.at), "text": e.text}
            if e.desc:
                ev["desc"] = e.desc
            if e.level:
                ev["status"] = e.level
            out[e.cr_id].append(ev)
        return out

    async def list(
        self,
        *,
        status: str | None = None,
        full: bool = False,
        kw: str = "",
        params: PageParams | None = None,
        focus_id: str | None = None,
        actor: CurrentUser | None = None,
    ) -> list[Row] | Row:
        """mock CHANGE_REQUESTS 顺序（单号倒序）。full=true 时每行再带 detail（CRDetail）
        和 events（评审追加记录，新的在前；mock reviewLog 按 detail.times 现算的节点由前端照旧算）。"""
        q = (
            select(ChangeRequest, OntologyRelease.ver)
            .outerjoin(OntologyRelease, OntologyRelease.id == ChangeRequest.release_id)
            .order_by(ChangeRequest.code.desc())
        )
        if status:
            q = q.where(ChangeRequest.status == CRStatus(status))
        if kw:
            q = q.where(
                or_(
                    ChangeRequest.title.contains(kw, autoescape=True),
                    ChangeRequest.code.contains(kw.upper(), autoescape=True),
                )
            )
        if params is not None:
            rows, total, actual = await StaticRepo(self.session).page(
                q,
                params,
                scalar=False,
                focus_id=focus_id,
                key=ChangeRequest.code,
                order=(ChangeRequest.code.desc(),),
            )
        else:
            rows = (await self.session.execute(q)).all()
        out = [self.row(cr, ver) for cr, ver in rows]
        if full:
            ids = [cr.id for cr, _ in rows]
            evs = await self._events(ids)
            names, changes = {}, {i: [] for i in ids}
            if ids:
                for target in await self.session.scalars(select(CrTarget).where(CrTarget.cr_id.in_(ids))):
                    names.setdefault(target.cr_id, {})[(target.target_kind, target.target_id)] = (
                        target.target_name
                    )
                for item in await self.session.scalars(
                    select(CrItem).where(CrItem.cr_id.in_(ids)).order_by(CrItem.seq, CrItem.id)
                ):
                    changes[item.cr_id].append(diff_row(item, names.get(item.cr_id, {})))
            for r, (cr, _) in zip(out, rows, strict=True):
                r["detail"] = (
                    {**cr.detail, "diffs": [*(cr.detail.get("diffs") or []), *changes[cr.id]]}
                    if changes[cr.id]
                    else cr.detail
                )
                r["events"] = evs[cr.id]
                r["detail"] = visible_analysis(r["detail"], actor)
        return Page.of(out, total, actual).model_dump() if params is not None else out

    async def _detail(self, cr: ChangeRequest, include_diffs: bool = True) -> dict[str, Any]:
        """CRDetail：种子 detail 原样 + cr_item 生成的改动行（接在 detail.diffs 后面，没有草稿行时不动）。"""
        if not include_diffs:
            return {**cr.detail, "diffs": []}
        extra = await CrWriteService(self.session).item_diffs(cr.id)
        if not extra:
            return cr.detail
        return {**cr.detail, "diffs": [*(cr.detail.get("diffs") or []), *extra]}

    async def detail(self, code: str, actor: CurrentUser | None = None, *, include_diffs: bool = True) -> Row:
        q = (
            select(ChangeRequest, OntologyRelease.ver)
            .outerjoin(OntologyRelease, OntologyRelease.id == ChangeRequest.release_id)
            .where(ChangeRequest.code == code)
        )
        hit = (await self.session.execute(q)).first()
        if hit is None:
            raise NotFound("变更单不存在", details={"id": code})
        cr, ver = hit
        targets = await CrWriteService(self.session).targets(cr.id)
        return {
            **self.row(cr, ver),
            "detail": visible_analysis(await self._detail(cr, include_diffs), actor),
            "events": (await self._events([cr.id]))[cr.id],
            "targets": [target_out(t) for t in targets],
            "lockVersion": cr.lock_version,
        }

    async def diffs(self, code: str, params: PageParams) -> Row:
        """Page legacy JSON ordinality before current CR seq/id, without reading the full CR or events."""
        cr_id = await self.session.scalar(select(ChangeRequest.id).where(ChangeRequest.code == code))
        if cr_id is None:
            raise NotFound("变更单不存在", details={"id": code})
        legacy = get_adapter(self.session).json_array_rows(ChangeRequest.detail["diffs"])
        old = (
            select(
                literal(0).label("source"),
                legacy.c.ordinality.label("seq"),
                literal(None, type_=BigInteger).label("item_id"),
                legacy.c.value.label("data"),
            )
            .select_from(ChangeRequest.__table__.join(legacy, true()))
            .where(ChangeRequest.id == cr_id)
        )
        current = select(
            literal(1).label("source"),
            CrItem.seq.label("seq"),
            CrItem.id.label("item_id"),
            literal(None, type_=JsonType).label("data"),
        ).where(CrItem.cr_id == cr_id)
        candidates = union_all(old, current).subquery()
        records, total, actual = await StaticRepo(self.session).page(
            select(candidates).order_by(candidates.c.source, candidates.c.seq, candidates.c.item_id),
            params,
            scalar=False,
        )
        ids = [row.item_id for row in records if row.item_id is not None]
        items = (
            {item.id: item for item in await self.session.scalars(select(CrItem).where(CrItem.id.in_(ids)))}
            if ids
            else {}
        )
        pairs = {(item.target_kind, item.target_id) for item in items.values()}
        names = {}
        if pairs:
            names = {
                (row.target_kind, row.target_id): row.target_name
                for row in await self.session.execute(
                    select(CrTarget.target_kind, CrTarget.target_id, CrTarget.target_name).where(
                        CrTarget.cr_id == cr_id, tuple_(CrTarget.target_kind, CrTarget.target_id).in_(pairs)
                    )
                )
            }
        rows = [
            diff_row(items[row.item_id], names) if row.item_id is not None else row.data for row in records
        ]
        return Page.of(rows, total, actual).model_dump()

    # ------------------------------------------------------------ 写
    async def _lock(self, code: str) -> ChangeRequest:
        cr = await self.session.scalar(
            select(ChangeRequest).where(ChangeRequest.code == code).with_for_update()
        )
        if cr is None:
            raise NotFound("变更单不存在", details={"id": code})
        return cr

    async def _next(self) -> str:
        vs = VersioningService()
        return next_ver(await vs.current_version(self.session), clock.now())

    def _move(
        self,
        cr: ChangeRequest,
        user: CurrentUser,
        to: CRStatus,
        action: str,
        text: str,
        desc: str | None = None,
        level: str | None = None,
    ) -> CrEvent:
        now = clock.now()
        cr.status = to
        cr.status_at = now
        event = CrEvent(
                cr_id=cr.id,
                action=action,
                actor=user.name,
                actor_code=user.id,
                created_by=user.id,
                text=f"{user.name} {text}",
                desc=desc,
                level=level,
                at=now,
        )
        self.session.add(event)
        return event

    async def transition(self, code: str, action: str, user: CurrentUser, reason: str | None = None) -> str:
        cr = await self._lock(code)
        await check_cr(self.session, cr, user)
        with self.session.no_autoflush:  # 只在最后 flush 一次：lockVersion 恰好 +1
            event = await self._transition(cr, action, user, reason)
        await self.session.flush()
        if event is not None:
            from app.modules.notifications.service import review_submitted

            await review_submitted(self.session, cr, event, user)
        return cr.code

    async def _transition(
        self, cr: ChangeRequest, action: str, user: CurrentUser, reason: str | None
    ) -> CrEvent | None:
        owner = is_cr_owner(cr, user)
        reviewer_id = (cr.detail or {}).get("reviewerCode")
        reviewer = user.id == reviewer_id if reviewer_id else cr.reviewer == user.name
        is_reviewer = reviewer and not owner  # Releases.vue isReviewer
        if action == "submit":
            _state(cr, S.draft)
            if not cr.changes:
                raise AppError("草稿里还没有改动，无法提交", code="CR_EMPTY", status_code=409)
            await CrWriteService(self.session).run_checks(
                cr, event=False, user=user
            )  # 服务端重算，不信 detail.checks
            _raise_blocked(cr)
            if not cr.reviewer:
                raise AppError("还没有指定审核人", code="CR_NO_REVIEWER", status_code=409)
            return self._move(cr, user, S.in_review, action, "提交审核", f"审核人 {cr.reviewer}")
        elif action == "withdraw":
            _state(cr, S.in_review)
            if not owner:
                raise Forbidden(f"只有提交人 {cr.owner} 能撤回提交")
            self._move(cr, user, S.draft, action, "撤回提交")
        elif action in ("approve", "reject"):
            _state(cr, S.in_review)
            if not is_reviewer:
                raise Forbidden(f"只有审核人 {cr.reviewer} 能审核，提交人不能审自己的单子")
            if action == "approve":
                await CrWriteService(self.session).run_checks(cr, event=False, user=user)
                _raise_blocked(cr)
                self._move(cr, user, S.approved, action, "审核通过", f"等待整版发布 {await self._next()}")
            else:
                reason = (reason or "").strip()
                if not reason:
                    raise AppError(
                        "请填写驳回原因",
                        code="VALIDATION_FAILED",
                        status_code=422,
                        details={"fields": [{"field": "rejectReason", "message": "必填"}]},
                    )
                cr.reject_reason = reason
                self._move(cr, user, S.rejected, action, "驳回", reason, "error")
        elif action in ("unapprove", "reopen"):
            _state(cr, S.approved if action == "unapprove" else S.rejected)
            if not (owner or reviewer):
                verb = "撤回" if action == "unapprove" else "改回草稿"
                raise Forbidden(f"只有 {cr.owner} 或 {cr.reviewer} 能{verb}")
            if action == "unapprove":
                self._move(cr, user, S.draft, action, "撤回审核结果", f"不进入 {await self._next()}")
            else:
                self._move(cr, user, S.draft, action, "改回草稿", "按驳回意见修改后重新提交")
        else:  # pragma: no cover - 路由只放行上面几种
            raise NotFound("未知操作")

    async def delete(self, code: str, user: CurrentUser) -> None:
        cr = await self._lock(code)
        await check_cr(self.session, cr, user)
        _state(cr, S.draft)
        if not is_cr_owner(cr, user):
            raise Forbidden(f"只有提交人 {cr.owner} 能删除")
        await self.session.delete(cr)
        await self.session.flush()
