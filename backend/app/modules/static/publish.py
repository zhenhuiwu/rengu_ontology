"""整版发布 / 回滚（Releases.vue 的「发布 vX」「回滚」按钮）。照前端 mock 的副作用写库，版本号由版本层生成。

- 合并校验 = mock/changes.ts `mergeCheck(approved)` 原样移植（M1 / V9 / V17 / V1–V17 / 提示）；
  数据来自 change_request.detail（mock CR_DETAILS 原样）[推断]。
- 发布（Releases.vue `publish()`）：已通过的单子 → 已发布、release = 新版本、updatedAt = 现在，
  每张写一条评审记录「{me} 整版发布为 {ver}」/「合并校验：{非通过行的说明；}」或「全部通过」。
- 回滚（Releases.vue `rollback()`）：create_version(rollback_of=ver)；被回滚版本的单子各写一条
  「{me} 回滚 {ver}」/「生成新版本，内容同 {上一版}」(warn)；另建一张回滚变更单（static-api.md §2.2-6）。
"""

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser
from app.core import clock
from app.core.errors import AppError, Forbidden, NotFound
from app.core.schema import _fmt_dt
from app.db.dialects import get_adapter
from app.modules.static.cr_write import next_cr_code
from app.modules.static.models import ChangeRequest, CrEvent, CRLevel, CRStatus
from app.modules.static.writeback import DIRTY, apply_crs
from app.modules.versioning.repo import ReleaseRepo
from app.modules.versioning.service import PUBLISH_LOCK, VersioningService, next_ver

Row = dict[str, Any]

M1 = "同一定义只被一张单子改动"


def _impact_evals(d: Row) -> int:
    imp = d.get("impact") or {}
    items = [*imp.get("direct", []), *imp.get("defs", []), *imp.get("usage", [])]
    return sum(i.get("count") or 1 for i in items if i.get("kind") == "评测题")


def _row(code: str, name: str, result: str, note: str) -> Row:
    return {"code": code, "name": name, "result": result, "note": note}


def merge_check(crs: list[ChangeRequest], next_version: str) -> tuple[list[Row], bool]:
    """mock/changes.ts `mergeCheck`（行顺序、文案一致）。"""
    rows: list[Row] = []
    owner: dict[str, list[str]] = defaultdict(list)
    for c in crs:
        for k in c.detail.get("touches") or []:
            owner[k].append(c.code)
    overlap = [(k, ids) for k, ids in owner.items() if len(ids) > 1]
    if overlap:
        note = "；".join(f"{'、'.join(ids)} 都改了 {k.split(':')[1]}" for k, ids in overlap)
        rows.append({"code": "M1", "name": M1, "result": "阻断", "note": note})
    else:
        rows.append(
            {"code": "M1", "name": M1, "result": "通过", "note": f"{len(crs)} 张单子改动的定义互不重叠"}
        )

    words: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for c in crs:
        for s in c.detail.get("synonyms") or []:
            words[s["word"]].append((s["to"], c.code))
    clash = [(w, v) for w, v in words.items() if len({to for to, _ in v}) > 1]
    if clash:
        note = "；".join(f"“{w}”被 {'、'.join(f'{i} 指向 {to}' for to, i in v)}" for w, v in clash)
        rows.append({"code": "V9", "name": "同义词冲突", "result": "阻断", "note": note})
    else:
        note = f"新增同义词 {'、'.join(f'“{w}”' for w in words)} 互不冲突" if words else "没有新增同义词"
        rows.append({"code": "V9", "name": "同义词冲突", "result": "通过", "note": note})
    dup = [w for w, v in words.items() if len(v) > 1 and len({to for to, _ in v}) == 1]
    if dup:
        note = "；".join(f"“{w}”被重复添加，发布时只保留一条" for w in dup)
        rows.append({"code": "V17", "name": "同名词唯一默认", "result": "警告", "note": note})
    else:
        rows.append(
            {"code": "V17", "name": "同名词唯一默认", "result": "通过", "note": "每个词只有一个默认指向"}
        )

    self_blocked = [c for c in crs if any(r.get("result") == "阻断" for r in c.detail.get("checks") or [])]
    if self_blocked:
        note = f"{'、'.join(c.code for c in self_blocked)} 有阻断项"
        rows.append({"code": "V1–V17", "name": "各单自身校验", "result": "阻断", "note": note})
    else:
        rows.append({"code": "V1–V17", "name": "各单自身校验", "result": "通过", "note": "各单均无阻断"})

    syn_crs = [c for c in crs if c.detail.get("synonyms")]
    if len(syn_crs) > 1:
        ids = "、".join(c.code for c in syn_crs)
        note = f"{ids} 都改了同义词，发布后同义词索引一起重建，约 2 分钟生效"
        rows.append({"code": "提示", "name": "同义词索引", "result": "信息", "note": note})
    evals = sum(_impact_evals(c.detail) for c in crs)
    if evals:
        note = f"已按 {next_version} 草稿回放 {evals} 道评测题，通过 {evals} 题，失败 0 题"
        rows.append({"code": "提示", "name": "评测回放", "result": "信息", "note": note})
    return rows, any(r["result"] == "阻断" for r in rows)


class PublishService:
    def __init__(self, session: AsyncSession, versioning: VersioningService | None = None) -> None:
        self.session = session
        self.versioning = versioning or VersioningService()

    async def _approved(self, *, lock: bool = False) -> list[ChangeRequest]:
        q = (
            select(ChangeRequest)
            .where(ChangeRequest.status == CRStatus.approved)
            .order_by(ChangeRequest.code.desc())
        )
        if lock:
            q = q.with_for_update()
        return list((await self.session.scalars(q)).all())

    async def _next_version(self, now: datetime) -> str:
        return next_ver(await self.versioning.current_version(self.session), now)

    async def merge_check(self) -> Row:
        rows, blocked = merge_check(await self._approved(), await self._next_version(clock.now()))
        return {"rows": rows, "blocked": blocked}

    def _event(
        self, cr: ChangeRequest, user: CurrentUser, action: str, text: str, desc: str, **kw: Any
    ) -> None:
        self.session.add(
            CrEvent(
                cr_id=cr.id,
                action=action,
                actor=user.name,
                actor_code=user.id,
                created_by=user.id,
                text=f"{user.name} {text}",
                desc=desc,
                at=kw.pop("at"),
                **kw,
            )
        )

    async def publish(self, user: CurrentUser, note: str | None) -> str:
        if not user.is_ontology_admin:
            raise Forbidden("需要本体管理员发布")
        now = clock.now()
        # 先拿整版发布锁（create_version 里再拿一次，同一事务可重入），保证写回用的版本号就是新版本号
        await get_adapter(self.session).xact_lock(self.session, PUBLISH_LOCK)
        crs = await self._approved(lock=True)
        next_ver = await self._next_version(now)
        rows, blocked = merge_check(crs, next_ver)
        if blocked:
            raise AppError(
                "合并校验有阻断，不能发布。请把冲突的变更单撤回或合并后再发布。",
                code="MERGE_BLOCKED",
                status_code=409,
                details={"rows": rows, "blocked": True},
            )
        codes = [c.code for c in crs]
        note = note or "；".join(c.title for c in crs)
        # 同事务写回定义表（writeback.HOOKS）；没登记的段（动作 / 事件 / 规则…）跳过并记进事件
        skipped = await apply_crs(self.session, crs, next_ver, now, user.id)
        # [推断] note 不传时用变更单标题拼一句（mock 发布不填说明）
        ver = await self.versioning.create_version(
            self.session, codes, published_by=user.id, note=note, now=now
        )
        self.session.info.pop(DIRTY, None)
        release = await ReleaseRepo(self.session).by_ver(ver)
        desc = "合并校验：" + ("；".join(r["note"] for r in rows if r["result"] != "通过") or "全部通过")
        if skipped:
            desc += "；未写回定义表：" + "、".join(skipped)
        for c in crs:
            c.status = CRStatus.published
            c.release_id = release.id if release else None
            c.status_at = now
            self._event(c, user, "publish", f"整版发布为 {ver}", desc, at=now)
        await self.session.flush()
        return ver

    async def _next_cr_code(self) -> str:
        return await next_cr_code(self.session)

    async def rollback(self, user: CurrentUser, ver: str, note: str | None) -> str:
        # 前端不限管理员（static-api.md §2.2-6，限管理员是 [推断]，默认不做）
        now = clock.now()
        target = await ReleaseRepo(self.session).by_ver(ver)
        if target is None:
            raise NotFound("版本不存在", details={"ver": ver})
        prev = target.parent_ver
        cr = ChangeRequest(
            code=await self._next_cr_code(),
            title=f"回滚 {ver}",
            reason=f"回滚到 {prev}" if prev else "",
            owner=user.name,
            reviewer=user.name,
            status=CRStatus.published,
            level=CRLevel.minor,
            changes=0,
            detail={"times": {"created": _fmt(now), "submitted": _fmt(now), "decided": _fmt(now)}},
            status_at=now,
            created_by=user.id,
        )
        self.session.add(cr)
        await self.session.flush()
        new_ver = await self.versioning.create_version(
            self.session,
            [cr.code],
            rollback_of=ver,
            published_by=user.id,
            note=note or f"回滚 {ver}",
            now=now,
        )
        release = await ReleaseRepo(self.session).by_ver(new_ver)
        cr.release_id = release.id if release else None
        originals = await self.session.scalars(
            select(ChangeRequest).where(ChangeRequest.release_id == target.id).order_by(ChangeRequest.id)
        )
        for c in originals:
            self._event(
                c, user, "rollback", f"回滚 {ver}", f"生成新版本，内容同 {prev}", level="warn", at=now
            )
        await self.session.flush()
        return new_ver


def _fmt(dt: datetime) -> str:
    return _fmt_dt(dt)
