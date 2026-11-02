from types import SimpleNamespace

from sqlalchemy import column, select, table, text
from sqlalchemy.exc import SQLAlchemyError

from app.common.auth import check_func
from app.common.ontology import require_ontology_id
from app.core.errors import AppError, NotFound
from app.db.dialects import get_adapter
from app.modules.action.models import ActionType, OntologyEditRule, SideEffect
from app.modules.data.models import ExternalReadBinding, GovernedBinding
from app.modules.dynamic.models import ActionRun, Alert, DecisionLog
from app.modules.dynamic.rules_models import Rule, RuleHit
from app.modules.event.models import Event, EventType
from app.modules.static.cr_analysis import definition_impacts
from app.modules.static.cr_write import HOLDING
from app.modules.static.models import ChangeRequest, ObjectType
from app.modules.static.writeback import DIRTY, _hard_delete
from app.modules.versioning.repo import ReleaseRepo
from app.modules.versioning.service import PUBLISH_LOCK, VersioningService

# Unknown database foreign keys must be reviewed before deleting an object.
KNOWN_FKS = {
    ('property', 'object_type_id'), ('object_view', 'object_type_id'),
    ('link_type', 'from_object_id'), ('link_type', 'to_object_id'),
    ('onto_qa_path', 'from_object_id'), ('onto_qa_path', 'to_object_id'),
    ('onto_qa_path_example', 'from_object_id'), ('onto_qa_path_example', 'to_object_id'),
    ('dimension', 'object_id'), ('lifecycle', 'object_type_id'),
    ('quality_check', 'object_type_id'), ('sys_role_scope', 'object_type_id'),
}

async def delete_object(session, user, code: str, lock_version: int, object_version: int):
    check_func(user, 'object-types', 'manage')
    check_func(user, 'releases', 'manage')
    require_ontology_id(session)
    if user.ontology_role not in ('owner', 'editor') or user.is_service_account or user.ontology_ids is not None:
        raise AppError('需要本体工作空间的编辑权限', code='FORBIDDEN', status_code=403)
    await get_adapter(session).xact_lock(session, PUBLISH_LOCK)
    await get_adapter(session).xact_lock(session, f'static.cr.target.object:{code}')
    latest = await ReleaseRepo(session).latest()
    if (latest.id if latest else 0) != lock_version:
        raise AppError('工作版本已变更，请刷新后重试', code='VERSION_CONFLICT', status_code=409)
    obj = await session.scalar(select(ObjectType).where(ObjectType.code == code).with_for_update())
    if obj is None:
        raise NotFound('对象类型不存在')
    if obj.lock_version != object_version:
        raise AppError('对象定义已变更，请刷新后重试', code='VERSION_CONFLICT', status_code=409)
    reasons = []
    if await session.scalar(select(ChangeRequest.id).where(ChangeRequest.status.in_(HOLDING)).limit(1)):
        reasons.append('存在未完成的定义草稿，无法确认引用，请先处理草稿')
    impact = await definition_impacts(session, [SimpleNamespace(target_kind='object', target_id=code, target_name=obj.display_name)])
    reasons.extend(f"{row['kind']}：{row['name']}" for row in impact['defs'])
    aliases = [s for s in (obj.code, obj.api_name, obj.display_name) if s]
    checks = [
        ('动作', select(ActionType.id).where((ActionType.target_kind == code) | ActionType.target_api.in_(aliases) | ActionType.target.in_(aliases))),
        ('动作编辑规则', select(OntologyEditRule.id).where(OntologyEditRule.obj.in_(aliases))),
        ('动作副作用', select(SideEffect.id).where(SideEffect.target.in_(aliases))),
        ('事件类型', select(EventType.id).where((EventType.obj_kind == code) | EventType.object_api.in_(aliases) | EventType.object.in_(aliases))),
        ('规则', select(Rule.id).where(Rule.obj_kind == code)),
        ('规则命中', select(RuleHit.id).where(RuleHit.obj_kind == code)),
        ('告警', select(Alert.id).where(Alert.obj_kind == code)),
        ('动作执行', select(ActionRun.id).where(ActionRun.obj_kind == code)),
        ('决策记录', select(DecisionLog.id).where(DecisionLog.obj_kind == code)),
        ('事件记录', select(Event.id).where(Event.obj_kind == code)),
        ('受控绑定', select(GovernedBinding.id).where(GovernedBinding.object_code == code)),
        ('外部读取绑定', select(ExternalReadBinding.id).where(ExternalReadBinding.object_code == code)),
    ]
    for label, query in checks:
        if await session.scalar(query.limit(1)):
            reasons.append(f'{label}引用该对象')
    # Inspect actual database constraints, not only ORM metadata. Composite or unknown FKs fail closed.
    fk_rows = (await session.execute(text("""
        SELECT ns.nspname, child.relname, a.attname, parent.relname, cardinality(c.conkey)
        FROM pg_constraint c JOIN pg_class child ON child.oid=c.conrelid
        JOIN pg_namespace ns ON ns.oid=child.relnamespace
        JOIN pg_class parent ON parent.oid=c.confrelid
        JOIN pg_attribute a ON a.attrelid=child.oid AND a.attnum=c.conkey[1]
        WHERE c.contype='f' AND c.confrelid IN ('public.object_type'::regclass, 'public.property'::regclass)
    """))).all()
    for schema, name, attr, parent, width in fk_rows:
        if schema != 'public' or width != 1 or parent != 'object_type' or (name, attr) not in KNOWN_FKS:
            reasons.append(f'无法确认外键引用安全：{schema}.{name}.{attr}')
            continue
        if name == 'property':
            continue
        ref = table(name, column(attr), schema=schema)
        if await session.scalar(select(1).select_from(ref).where(ref.c[attr] == obj.id).limit(1)):
            reasons.append(f'{name}.{attr} 引用该对象')
    if reasons:
        raise AppError('无法删除：' + '；'.join(dict.fromkeys(reasons)), code='OBJECT_DELETE_BLOCKED', status_code=409)
    try:
        await _hard_delete(session, ObjectType, [obj])
        session.info.setdefault(DIRTY, set()).update({'objectTypes', 'properties'})
        service = VersioningService()
        base = latest.ver if latest else None
        snapshot = await service.provider.build_snapshot(session, base, [])
        if isinstance(snapshot.get('objectBindings'), dict):
            snapshot['objectBindings'] = {k: v for k, v in snapshot['objectBindings'].items() if k != code}
        release = await service._append(session, base, snapshot, [], {}, published_by=user.id,
                                        note=f'删除对象类型 {code}', now=None, latest=latest, scope='working')
    except SQLAlchemyError:
        raise AppError('无法安全删除对象，请检查关联引用后重试', code='OBJECT_DELETE_BLOCKED', status_code=409) from None
    return {'ver': release.ver}
