"""VersionService 的实现（契约见 app/contracts/versioning.py）。"""

import hashlib
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.outbox import RELEASE_PUBLISHED, emit
from app.contracts.versioning import ReleaseInfo, Snapshot, SnapshotProvider
from app.core import clock
from app.core.config import get_settings
from app.core.errors import AppError, EmptyRelease
from app.db.dialects import get_adapter
from app.modules.admin.models import SysUser
from app.modules.versioning.models import OntologyRelease, ReleaseKind
from app.modules.versioning.repo import ReleaseRepo
from app.modules.versioning.sections import restore_sections

PUBLISH_LOCK = "ontology.publish"


def snapshot_digest(snapshot: Snapshot) -> str:
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def next_ver(current: str | None, now: datetime) -> str:
    """vYYYY.MM.N：同月递增 N，跨月从 1 开始（按业务时区算月份）。"""
    local = now.astimezone(ZoneInfo(get_settings().display_tz))
    prefix = f"v{local:%Y.%m}."
    if current and current.startswith(prefix):
        return f"{prefix}{int(current.rsplit('.', 1)[1]) + 1}"
    return f"{prefix}1"


def _info(r: OntologyRelease) -> ReleaseInfo:
    return ReleaseInfo(
        ver=r.ver,
        parent_ver=r.parent_ver,
        published_at=r.published_at,
        published_by=r.published_by,
        note=r.note,
        change_order_ids=tuple(r.change_order_ids or ()),
        snapshot_sha256=r.snapshot_sha256,
        rollback_of=r.rollback_of,
        published_by_code=r.published_by_code,
    )


class VersioningService:
    def __init__(self, provider: SnapshotProvider | None = None) -> None:
        if provider is None:
            from app.modules.versioning.sections import SectionSnapshotProvider

            provider = SectionSnapshotProvider()
        self.provider: SnapshotProvider = provider

    async def create_formal(self, session: AsyncSession, *, user, note: str, lock_version: int) -> str:
        from app.contracts.versioning import SECTION_OWNERS
        from app.modules.versioning.sections import registered_sections
        from app.modules.static.release_readiness import object_structure

        await get_adapter(session).xact_lock(session, PUBLISH_LOCK)
        latest = await ReleaseRepo(session).latest()
        if (latest.id if latest else 0) != lock_version:
            raise AppError("本体已在别处变更，请刷新后重新确认", code="VERSION_CONFLICT", status_code=409)
        builders = registered_sections()
        issues = [{"section": name, "id": "", "message": "快照构建器未登记"} for name in SECTION_OWNERS if name not in builders]
        snapshot = {name: await builder.build_section(session, None, []) for name, builder in builders.items()}
        from sqlalchemy import text
        from sqlalchemy.orm import undefer
        from app.common.ontology import require_ontology_id
        from app.common.sql_guard import ensure_governed_tables, governed_tables
        from app.modules.data.models import GovernedBinding

        oid = require_ontology_id(session)
        await get_adapter(session).xact_lock(session, f"governed.binding.{oid}")
        await ensure_governed_tables(session)
        has_extra = await session.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid=to_regclass('public.governed_binding') AND attname='supplementary' AND NOT attisdropped)"))
        query = select(GovernedBinding).where(GovernedBinding.ontology_id == oid).order_by(GovernedBinding.object_code)
        bindings = list(await session.scalars(query.options(undefer(GovernedBinding.supplementary)) if has_extra else query))
        governed = governed_tables(oid, session)
        snapshot["objectBindings"] = {}
        for binding in bindings:
            extra = binding.supplementary if has_extra else None
            live = governed.get(binding.table_name)
            if not live or live.object_type != binding.object_code or (extra and binding.object_code not in session.info.get("supplementary_bindings", {})):
                issues.append({"section": "objectBindings", "id": binding.object_code, "message": "受控绑定当前无法验证"})
                continue
            snapshot["objectBindings"][binding.object_code] = {
                "table": binding.table_name, "readColumns": list(binding.read_columns),
                "writeColumns": list(binding.write_columns), "supplementary": extra,
            }
        from app.common.auth import check_func
        from app.common.ontology import require_ontology_id
        from app.modules.functions.models import FnVersion, Function

        ontology_id = require_ontology_id(session)
        functions = list(await session.scalars(select(Function).where(
            Function.ontology_id == ontology_id, Function.status == "published"
        ).order_by(Function.code)))
        if functions:
            check_func(user, "functions", "view")
        snapshot["functions"] = {}
        for fn in functions:
            versions = list(await session.scalars(select(FnVersion).where(
                FnVersion.ontology_id == ontology_id, FnVersion.function_id == fn.id
            ).order_by(FnVersion.id)))
            if not versions or any(not isinstance(v.snapshot, dict) or not v.snapshot for v in versions):
                issues.append({"section": "functions", "id": fn.code, "message": "已发布函数缺少版本定义快照"})
                continue
            snapshot["functions"][fn.code] = {
                "currentVersion": versions[-1].ver,
                "versions": {v.ver: v.snapshot for v in versions},
            }
        for name in SECTION_OWNERS:
            if name in snapshot and snapshot[name] is None:
                issues.append({"section": name, "id": "", "message": "快照内容缺失"})
        objects = snapshot.get("objectTypes") or {}
        properties = snapshot.get("properties") or {}
        for code, obj in objects.items():
            if obj.get("status") == "已发布":
                result, message = object_structure(obj, properties.get(code) or {})
                if result != "通过":
                    issues.append({"section": "objectTypes", "id": code, "message": message})
        for section in ("actionTypes", "eventTypes", "rules"):
            for code, definition in (snapshot.get(section) or {}).items():
                if definition.get("status") not in ("已发布", "published", "变更中"):
                    continue
                target = definition.get("targetKind") if section == "actionTypes" else definition.get("objKind")
                if section == "eventTypes" and code in ("ActionExecuted", "ActionOutcomeUnknown") and definition.get("objectApi") == "ActionRun":
                    continue  # Platform events refer to the execution record, not a user object type.
                if not target or target not in objects or objects[target].get("status") != "已发布":
                    issues.append({"section": section, "id": code, "message": "目标对象未明确绑定已发布对象"})
        for code, link in (snapshot.get("links") or {}).items():
            if link.get("status") == "已发布" and any(
                not objects.get(link.get(end)) or objects[link[end]].get("status") != "已发布" for end in ("from", "to")
            ):
                issues.append({"section": "links", "id": code, "message": "关系两端须为已发布对象"})
        from app.modules.dynamic.rule_editor import validate_definition
        for code, definition in (snapshot.get("rules") or {}).items():
            if definition.get("status") in ("已发布", "published", "变更中"):
                try:
                    await validate_definition(session, user, definition)
                except AppError as error:
                    issues.append({"section": "rules", "id": code, "message": error.message})
        from app.modules.event.ingress import dedup_window
        for code, definition in (snapshot.get("eventTypes") or {}).items():
            if definition.get("status") == "已发布":
                try:
                    dedup_window(definition.get("dedupWindow") or "")
                except AppError as error:
                    issues.append({"section": "eventTypes", "id": code, "message": error.message})
        from app.modules.static.metric_trial import compile_metric, execution_bindings
        from app.modules.static.models import Metric, MetricStatus, ObjectType
        await ensure_governed_tables(session)
        object_rows = list(await session.scalars(select(ObjectType)))
        metrics = await session.scalars(select(Metric).where(Metric.status == MetricStatus.published))
        for metric in metrics:
            definition = {"formula": metric.formula, "def": metric.def_rows}
            try:
                compile_metric(definition, object_rows, require_ontology_id(session),
                               await execution_bindings(session, object_rows, definition), session=session)
            except AppError as error:
                issues.append({"section": "metrics", "id": metric.code, "message": error.message})
        from app.modules.action.condition_expr import Unavailable
        from app.modules.action.writeback import declaration, validate_edit, writeback_mapping
        from app.modules.integration.action_edits import validate_source_definition
        from app.modules.integration.action_effects import STANDARD, effect_timing
        from app.modules.integration.http_mapping import validate_mapping
        from app.modules.integration.models import ExtSystem
        from app.modules.integration.service import is_writeback_target
        for code, action in (snapshot.get("actionTypes") or {}).items():
            if action.get("status") != "已发布":
                continue
            try:
                params = action.get("params") or []
                validate_mapping(action.get("httpMapping"), {p["api"] for p in params})
                system = await session.scalar(select(ExtSystem).where(ExtSystem.code == action.get("sysId"))) if action.get("sysId") else None
                if action.get("sysId") and (system is None or not is_writeback_target(system)):
                    raise AppError("动作关联的外部系统不可写回", code="FORMAL_RELEASE_BLOCKED", status_code=409)
                for edit in (action.get("writeback") or {}).get("edits", []):
                    if declaration(edit.get("field")):
                        if system is None or system.category != "Webhook":
                            raise AppError("声明式写回须绑定有效 Webhook 系统", code="FORMAL_RELEASE_BLOCKED", status_code=409)
                    else:
                        await validate_edit(session, edit.get("op"), edit.get("obj"), edit.get("field"))
                for effect in (action.get("writeback") or {}).get("effects", []):
                    if effect.get("kind") == "事件" and str(effect.get("target") or "").split(" ", 1)[0] in STANDARD:
                        continue
                    effect_timing(effect)
                    spec = writeback_mapping(effect.get("note"))
                    if not isinstance(spec.get("payload"), dict):
                        raise AppError("副作用缺少载荷映射", code="FORMAL_RELEASE_BLOCKED", status_code=409)
                    if effect.get("kind") == "事件":
                        event = (snapshot.get("eventTypes") or {}).get(effect.get("target"))
                        if set(spec) != {"version", "object", "payload"} or not event or event.get("status") != "已发布" or event.get("version") != spec["version"]:
                            raise AppError("副作用事件版本不可用", code="FORMAL_RELEASE_BLOCKED", status_code=409)
                        fields = {f["api"]: f for f in event.get("payload") or []}
                        if set(spec["payload"]) - fields.keys() or any(f.get("required") and api not in spec["payload"] for api, f in fields.items()):
                            raise AppError("副作用载荷与事件定义不一致", code="FORMAL_RELEASE_BLOCKED", status_code=409)
                    elif effect.get("kind") not in ("通知", "Webhook") or set(spec) != {"payload"}:
                        raise AppError("副作用类型或载荷映射无效", code="FORMAL_RELEASE_BLOCKED", status_code=409)
                    for source in spec["payload"].values():
                        await validate_source_definition(session, source)
            except (AppError, Unavailable, KeyError, TypeError, ValueError) as error:
                issues.append({"section": "actionTypes", "id": code, "message": getattr(error, "message", str(error))})
        from app.modules.event.payload_schema import validate_definition as validate_payload_definition
        from app.modules.event.schemas import FieldOut
        from app.modules.event.simulation import filter_matches
        for code, event in (snapshot.get("eventTypes") or {}).items():
            if event.get("status") != "已发布":
                continue
            try:
                fields = [FieldOut.model_validate(field) for field in event.get("payload") or []]
                for field in fields:
                    validate_payload_definition(field.type, field.schema)
                payload = {f.api: 0 if f.type in ("整数", "金额", "百分比", "数值", "小数") else False if f.type == "布尔" else "" for f in fields}
                for sub in event.get("subscribers") or []:
                    if sub.get("status") != "启用":
                        continue
                    filter_matches(sub.get("filter") or "", fields, payload)
                    if sub.get("kind") == "规则":
                        rule = (snapshot.get("rules") or {}).get(sub.get("ruleId"))
                        trigger = (rule or {}).get("trigger") or {}
                        if not rule or rule.get("objKind") != event.get("objKind") or trigger.get("kind") != "事件" or trigger.get("eventId") != code:
                            raise AppError("启用订阅未绑定同对象的事件触发规则", code="FORMAL_RELEASE_BLOCKED", status_code=409)
            except (AppError, Unavailable, KeyError, TypeError, ValueError) as error:
                issues.append({"section": "eventTypes", "id": code, "message": getattr(error, "message", str(error))})
        if issues:
            raise AppError("正式发布校验未通过", code="FORMAL_RELEASE_BLOCKED", status_code=409, details={"issues": issues})
        release = await self._append(session, latest.ver if latest else None, snapshot, [],
            {"changeOrders": 0, "snapshot": "complete"}, published_by=user.id, note=note,
            now=None, latest=latest, scope="formal")
        return release.ver

    async def create_version(
        self,
        session: AsyncSession,
        change_order_ids: list[str],
        *,
        rollback_of: str | None = None,
        published_by: str,
        note: str,
        now: datetime | None = None,
    ) -> str:
        """唯一入口。发布与回滚都生成新版本（只追加，不改任何旧行），同事务、不 commit。

        - 普通发布（rollback_of=None）：依次调用各模块登记的 section builder 组装快照；
        - 回滚（rollback_of=被回滚的版本号，必须是当前最新版）：**不调用** builder，
          快照原样复制它的上一版（restoredFrom），change_order_ids 是那张回滚变更单。
        """
        if not change_order_ids:
            raise EmptyRelease()
        await get_adapter(session).xact_lock(session, PUBLISH_LOCK)
        repo = ReleaseRepo(session)
        latest = await repo.latest()
        base_ver = latest.ver if latest else None
        cr_ids = list(change_order_ids)
        if rollback_of is None:
            snapshot = await self.provider.build_snapshot(session, base_ver, cr_ids)
            summary: dict = {"changeOrders": len(cr_ids)}
        else:
            # 前端规则：「只能回滚最近一次发布」
            if latest is not None and latest.ver == rollback_of and latest.parent is None:
                # 首个发布版本没有上一版可恢复：单独的错误码，前端据此隐藏回滚按钮
                raise AppError(
                    "这是首个发布版本，没有可回滚到的上一版",
                    code="ROLLBACK_FIRST_RELEASE",
                    status_code=409,
                    details={"current": base_ver, "rollbackOf": rollback_of},
                )
            if latest is None or latest.ver != rollback_of:
                raise AppError(
                    "只能回滚最近一次发布",
                    code="ROLLBACK_NOT_LATEST",
                    status_code=409,
                    details={"current": base_ver, "rollbackOf": rollback_of},
                )
            target = latest.parent
            snapshot = dict(target.snapshot)
            summary = {"changeOrders": len(cr_ids), "rollbackOf": rollback_of, "restoredFrom": target.ver}
        release = await self._append(
            session,
            base_ver,
            snapshot,
            cr_ids,
            summary,
            published_by=published_by,
            note=note,
            now=now,
            latest=latest,
            rollback=rollback_of is not None,
            scope="working",
        )
        if rollback_of is not None:
            # 先写新版本行，再让各模块把定义表恢复成被恢复的那一版：钩子拿到这次回滚版本的 id
            # （Palantir 把快照里没有的类型的 retired_release_id 写成它，action-event-api.md:325）。同一事务。
            await restore_sections(session, snapshot, release.id)
        return release.ver

    async def _append(
        self,
        session: AsyncSession,
        base_ver: str | None,
        snapshot: Snapshot,
        change_order_ids: list[str],
        summary: dict,
        *,
        published_by: str,
        note: str,
        now: datetime | None,
        latest: OntologyRelease | None,
        rollback: bool = False,
        scope: str = "working",
    ) -> OntologyRelease:
        now = now or clock.now()
        ver = next_ver(base_ver, now)
        publisher = await session.scalar(select(SysUser).where(SysUser.code == published_by))
        if publisher is None:
            raise AppError(
                "发布人不存在", code="USER_NOT_FOUND", status_code=422, details={"userId": published_by}
            )
        release = await ReleaseRepo(session).add(
            OntologyRelease(
                ver=ver,
                scope=scope,
                parent=latest,  # parent_id = 上一版的 id
                kind=ReleaseKind.rollback if rollback else ReleaseKind.publish,
                rollback_target=latest if rollback else None,  # rollback_of_id
                snapshot=snapshot,
                snapshot_sha256=snapshot_digest(snapshot),
                change_order_ids=change_order_ids,
                change_summary=summary,
                note=note,
                publisher=publisher,
                published_at=now,
            )
        )
        # 同一事务写 outbox：其它模块只订阅 release.published（PDE 定）
        await emit(
            session,
            RELEASE_PUBLISHED,
            {
                "ver": ver,
                "parentVer": base_ver,
                "kind": "rollback" if rollback else "release",
                "rollbackOf": base_ver if rollback else None,
                "changeOrderIds": list(change_order_ids),
            },
            event_id=f"ontology_release:{release.ver}:{RELEASE_PUBLISHED}",
        )
        return release

    async def current_version(self, session: AsyncSession) -> str | None:
        latest = await ReleaseRepo(session).latest()
        return latest.ver if latest else None

    async def get_release(self, session: AsyncSession, ver: str) -> ReleaseInfo | None:
        r = await ReleaseRepo(session).by_ver(ver)
        return _info(r) if r else None

    async def get_snapshot(self, session: AsyncSession, ver: str | None = None) -> Snapshot | None:
        repo = ReleaseRepo(session)
        r = await (repo.by_ver(ver) if ver else repo.latest())
        return dict(r.snapshot) if r else None


def get_version_service() -> VersioningService:
    # 快照 = 各模块登记的 section builder 拼成（app/modules/versioning/sections.py）
    return VersioningService()
