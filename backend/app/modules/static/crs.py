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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser
from app.core import clock
from app.core.errors import AppError, Forbidden, NotFound
from app.core.schema import _fmt_dt
from app.modules.static.cr_write import CrWriteService, target_out
from app.modules.static.models import ChangeRequest, CrEvent, CRStatus
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
    def _row(cr: ChangeRequest, ver: str | None) -> Row:
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
        if cr.reject_reason:
            r["rejectReason"] = cr.reject_reason
        return r

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

    async def list(self, *, status: str | None = None, full: bool = False) -> list[Row]:
        """mock CHANGE_REQUESTS 顺序（单号倒序）。full=true 时每行再带 detail（CRDetail）
        和 events（评审追加记录，新的在前；mock reviewLog 按 detail.times 现算的节点由前端照旧算）。"""
        q = (
            select(ChangeRequest, OntologyRelease.ver)
            .outerjoin(OntologyRelease, OntologyRelease.id == ChangeRequest.release_id)
            .order_by(ChangeRequest.code.desc())
        )
        if status:
            q = q.where(ChangeRequest.status == CRStatus(status))
        rows = (await self.session.execute(q)).all()
        out = [self._row(cr, ver) for cr, ver in rows]
        if full:
            evs = await self._events([cr.id for cr, _ in rows])
            for r, (cr, _) in zip(out, rows, strict=True):
                r["detail"] = await self._detail(cr)
                r["events"] = evs[cr.id]
        return out

    async def _detail(self, cr: ChangeRequest) -> dict[str, Any]:
        """CRDetail：种子 detail 原样 + cr_item 生成的改动行（接在 detail.diffs 后面，没有草稿行时不动）。"""
        extra = await CrWriteService(self.session).item_diffs(cr.id)
        if not extra:
            return cr.detail
        return {**cr.detail, "diffs": [*(cr.detail.get("diffs") or []), *extra]}

    async def detail(self, code: str) -> Row:
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
            **self._row(cr, ver),
            "detail": await self._detail(cr),
            "events": (await self._events([cr.id]))[cr.id],
            "targets": [target_out(t) for t in targets],
            "lockVersion": cr.lock_version,
        }

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
    ) -> None:
        now = clock.now()
        cr.status = to
        cr.status_at = now
        self.session.add(
            CrEvent(
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
        )

    async def transition(self, code: str, action: str, user: CurrentUser, reason: str | None = None) -> str:
        cr = await self._lock(code)
        with self.session.no_autoflush:  # 只在最后 flush 一次：lockVersion 恰好 +1
            await self._transition(cr, action, user, reason)
        await self.session.flush()
        return cr.code

    async def _transition(
        self, cr: ChangeRequest, action: str, user: CurrentUser, reason: str | None
    ) -> None:
        owner, reviewer = cr.owner == user.name, cr.reviewer == user.name
        is_reviewer = reviewer and not owner  # Releases.vue isReviewer
        if action == "submit":
            _state(cr, S.draft)
            if not cr.changes:
                raise AppError("草稿里还没有改动，无法提交", code="CR_EMPTY", status_code=409)
            await CrWriteService(self.session).run_checks(cr, event=False)  # 服务端重算，不信 detail.checks
            _raise_blocked(cr)
            if not cr.reviewer:
                raise AppError("还没有指定审核人", code="CR_NO_REVIEWER", status_code=409)
            self._move(cr, user, S.in_review, action, "提交审核", f"审核人 {cr.reviewer}")
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
                await CrWriteService(self.session).run_checks(cr, event=False)
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
        _state(cr, S.draft)
        if cr.owner != user.name:
            raise Forbidden(f"只有提交人 {cr.owner} 能删除")
        await self.session.delete(cr)
        await self.session.flush()
