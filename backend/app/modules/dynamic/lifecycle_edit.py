"""Create or replace a lifecycle definition atomically, without a review workflow."""

from typing import Literal

from pydantic import Field, model_validator
from sqlalchemy import func, select

from app.common.auth import check_func
from app.common.ontology import require_ontology_id
from app.core.clock import now
from app.core.errors import AppError, NotFound
from app.core.schema import ApiModel
from app.db.dialects import get_adapter
from app.modules.dynamic.rules_models import Lifecycle, LifecycleDraft, LifecycleVersion
from app.modules.dynamic.rules_schemas import JsonInput
from app.modules.dynamic.sections import ensure_machine, lifecycle_def
from app.modules.dynamic.writeback import apply_edits, validate_transition_refs
from app.modules.static.models import ObjectStatus, ObjectType, Property
from app.modules.versioning.repo import ReleaseRepo
from app.modules.versioning.service import PUBLISH_LOCK, VersioningService


class StateIn(ApiModel):
    key: str = Field(min_length=1, max_length=64, pattern=r"^[^\x00]+$")
    name: str = Field(min_length=1, max_length=64, pattern=r"^[^\x00]+$")
    kind: Literal["初始", "中间", "终态"]
    desc: str = Field(default="", max_length=2000)
    sla: str | None = Field(default=None, max_length=200)
    x: int = Field(default=0, ge=-100000, le=100000, strict=True)
    y: int = Field(default=0, ge=-100000, le=100000, strict=True)


class TransitionIn(ApiModel):
    id: str = Field(min_length=1, max_length=16, pattern=r"^[^\x00]+$")
    from_: str = Field(alias="from", min_length=1, max_length=64)
    to: str = Field(min_length=1, max_length=64)
    trigger: Literal["事件", "动作", "系统", "人工"]
    by: str = Field(min_length=1, max_length=200)
    event_id: str | None = Field(default=None, max_length=64)
    action_id: str | None = Field(default=None, max_length=64)
    guard: str = Field(default="", max_length=2000)
    effect: str = Field(default="", max_length=2000)


class LifecycleSaveIn(JsonInput):
    field: str = Field(min_length=1, max_length=64, pattern=r"^[^\x00]+$")
    desc: str = Field(default="", max_length=2000)
    expected_version: int = Field(ge=0, strict=True)
    release_lock_version: int = Field(ge=0, strict=True)
    states: list[StateIn] = Field(min_length=1, max_length=200)
    transitions: list[TransitionIn] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def valid_machine(self):
        if sum(s.kind == "初始" for s in self.states) != 1:
            raise ValueError("生命周期必须有且只有一个初始状态")
        keys = {s.key for s in self.states}
        if len(keys) != len(self.states) or any(not s.key.strip() or not s.name.strip() for s in self.states):
            raise ValueError("状态值须非空且不能重复，状态名称不能为空")
        if len({t.id for t in self.transitions}) != len(self.transitions):
            raise ValueError("迁移编号不能重复")
        terminal = {s.key for s in self.states if s.kind == "终态"}
        for t in self.transitions:
            if t.from_ not in keys or t.to not in keys:
                raise ValueError("迁移的原状态和新状态须在当前生命周期中")
            if t.from_ in terminal:
                raise ValueError("终态不能设置离开的迁移")
            if not t.by.strip():
                raise ValueError("请填写触发来源")
            if t.trigger == "事件" and (not t.event_id or t.action_id):
                raise ValueError("事件迁移须选择事件类型")
            if t.trigger == "动作" and (not t.action_id or t.event_id):
                raise ValueError("动作迁移须选择动作类型")
            if t.trigger in ("系统", "人工") and (t.action_id or t.event_id):
                raise ValueError("系统或人工迁移不能绑定事件/动作类型")
        return self


async def save_lifecycle(session, user, code: str, body: LifecycleSaveIn):
    check_func(user, "object-types", "manage")
    check_func(user, "releases", "manage")
    require_ontology_id(session)
    if user.ontology_role not in ("owner", "editor") or user.is_service_account or user.ontology_ids is not None:
        raise AppError("需要本体工作空间的编辑权限", code="FORBIDDEN", status_code=403)
    await get_adapter(session).xact_lock(session, PUBLISH_LOCK)
    latest = await ReleaseRepo(session).latest()
    if (latest.id if latest else 0) != body.release_lock_version:
        raise AppError("本体已在别处变更，请刷新后重新编辑", code="VERSION_CONFLICT", status_code=409)
    obj = await session.scalar(select(ObjectType).where(
        ObjectType.code == code, ObjectType.status == ObjectStatus.published
    ).with_for_update())
    if obj is None:
        raise NotFound("对象类型不存在或尚未保存")
    prop = await session.scalar(select(Property).where(Property.object_type_id == obj.id, Property.api_name == body.field))
    if prop is None or (prop.data_type.lower() not in ("string", "text", "enum", "varchar", "char", "nvarchar") and not prop.enum_name):
        raise AppError("请选择当前对象已定义的文本或枚举状态属性", code="VALIDATION_FAILED", status_code=422)
    lc = await session.scalar(select(Lifecycle).where(Lifecycle.object_type_id == obj.id))
    current_version = (lc.version or 0) if lc else 0
    if current_version != body.expected_version:
        raise AppError("生命周期已被修改，请刷新后重新编辑", code="VERSION_CONFLICT", status_code=409)
    if lc and await session.scalar(select(LifecycleDraft.id).where(LifecycleDraft.lifecycle_id == lc.id).limit(1)):
        raise AppError("生命周期仍有旧草稿，请先处理旧草稿", code="LIFECYCLE_DRAFT_EXISTS", status_code=409)
    historical = await session.scalar(select(func.max(LifecycleVersion.version)).where(
        LifecycleVersion.lifecycle_id == lc.id
    )) if lc else 0
    version = max(historical or 0, current_version) + 1
    edits = {"addStates": [s.model_dump(by_alias=True) for s in body.states],
             "addTransitions": [t.model_dump(by_alias=True, exclude_none=True) for t in body.transitions]}
    await validate_transition_refs(session, edits)
    for field, model_name in (("actionId", "action"), ("eventId", "event")):
        if model_name == "action":
            from app.modules.action.models import ActionType as model
            target = model.target_kind
        else:
            from app.modules.event.models import EventType as model
            target = model.obj_kind
        ids = {t[field] for t in edits["addTransitions"] if t.get(field)}
        if ids:
            valid = set(await session.scalars(select(model.code).where(model.code.in_(ids), target == code)))
            if ids - valid:
                raise AppError("迁移的事件或动作须属于当前对象", code="VALIDATION_FAILED", status_code=422)
    current = await lifecycle_def(session, lc) if lc and lc.version else {"states": []}
    counts = {s["key"]: s.get("count", 0) for s in current["states"]}
    for state in edits["addStates"]:
        state["count"] = counts.get(state["key"], 0)
    machine = apply_edits({"states": [], "transitions": []}, edits, code=code, version=version)
    at = now()
    if lc is None:
        lc = Lifecycle(code=code, object_type_id=obj.id, object_name=obj.display_name,
                       field=body.field, owner=user.name, updated_on=at, desc=body.desc, is_seed=False)
        session.add(lc)
        await session.flush()
    lc.field, lc.desc, lc.version, lc.updated_on = body.field, body.desc, version, at
    lc.object_name = obj.display_name
    session.add(LifecycleVersion(code=f"{code}:v{version}", lifecycle_id=lc.id, version=version,
                                at=at, by=user.name, change="保存生命周期定义", snapshot=machine, is_seed=False))
    await session.flush()
    await ensure_machine(session, lc, {"version": version, **machine}, release_id=None, by=user.name)
    service = VersioningService()
    base = latest.ver if latest else None
    snapshot = await service.provider.build_snapshot(session, base, [])
    release = await service._append(session, base, snapshot, [], {"lifecycles": [code]},
                                    published_by=user.id, note=f"保存生命周期 {obj.display_name}",
                                    now=at, latest=latest, scope="working")
    return {"ver": release.ver, "version": version}
