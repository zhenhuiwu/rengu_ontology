"""Explicit local database bindings, validated every time they are loaded."""
import re

from sqlalchemy import Integer, MetaData, String, Table, select, text
from sqlalchemy.engine import make_url
from app.core.config import get_settings
from app.core.errors import AppError
from app.db.base import Base
from app.modules.data.models import Datasource, DsType, GovernedBinding
from app.modules.static.models import ObjectStatus, ObjectType


def invalid(message):
    return AppError(message, code="BINDING_UNAVAILABLE", status_code=422)


def policy_safe(value):
    if not isinstance(value, str):
        return False
    compact = re.sub(r"[\s()]|::text", "", value.lower())
    return compact == "ontology_id=nullifcurrent_setting'rengu.ontology_id',true,''::bigint"


async def inspect_table(session, name):
    import app.db.models  # noqa: F401
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", name) or name in Base.metadata.tables:
        raise invalid("只能绑定非平台的 public 业务表")
    status = (await session.execute(text("""
        SELECT c.relrowsecurity, c.relforcerowsecurity, c.relkind,
               has_table_privilege(current_user, c.oid, 'SELECT'),
               has_table_privilege(current_user, c.oid, 'UPDATE')
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname=:name
    """), {"name": name})).one_or_none()
    policies = (await session.execute(text("""
        SELECT permissive, roles, cmd, qual, with_check FROM pg_policies
        WHERE schemaname='public' AND tablename=:name
    """), {"name": name})).all()
    if not status or not all(status[:2]) or status[2] not in ('r', b'r') or not status[3] or len(policies) != 1:
        raise invalid("业务表必须存在、可读取且启用强制工作空间 RLS")
    permissive, roles, cmd, qual, check = policies[0]
    if permissive != 'PERMISSIVE' or list(roles) != ['public'] or cmd != 'ALL' or not policy_safe(qual) or not policy_safe(check):
        raise invalid("业务表必须仅配置标准工作空间隔离策略")
    conn = await session.connection()
    try:
        table = await conn.run_sync(lambda sync: Table(name, MetaData(), autoload_with=sync))
    except Exception:
        raise invalid("无法读取本机业务表结构") from None
    if not {'id', 'ontology_id'} <= set(table.c.keys()):
        raise invalid("业务表缺少内部行 id 或 ontology_id")
    if not table.c.id.primary_key or not isinstance(table.c.id.type, Integer):
        raise invalid("业务表内部行 id 必须是整数主键")
    return table, bool(status[4])


async def validate_binding(session, obj, name, reads, writes):
    if obj.status != ObjectStatus.published or obj.source_table not in (name, f"public.{name}") or not obj.datasource_name:
        raise invalid("对象须已发布并明确映射到此源表和数据连接")
    source = await session.scalar(select(Datasource).where(Datasource.ontology_id == obj.ontology_id, (Datasource.code == obj.datasource_name) | (Datasource.name == obj.datasource_name)))
    database = await session.scalar(text("SELECT current_database()"))
    if not source or source.type != DsType.postgresql or source.database != database:
        raise invalid("仅支持与本应用同库的 PostgreSQL 数据源")
    try:
        settings = get_settings()
        main = make_url(settings.database_url)
        readonly = make_url(settings.qa_readonly_database_url or "")
        if (main.host, main.port or 5432, main.database) != (readonly.host, readonly.port or 5432, readonly.database):
            raise ValueError()
        if (source.host, source.port, source.database) != (main.host, main.port or 5432, main.database):
            raise ValueError()
    except Exception:
        raise invalid("问数只读连接必须指向应用本库") from None
    if not isinstance(reads, list) or not isinstance(writes, list) or len(reads) > 100 or len(writes) > 100 or any(not isinstance(v, str) for v in reads + writes):
        raise invalid("字段配置无效")
    if len(set(reads)) != len(reads) or len(set(writes)) != len(writes) or not set(writes) <= set(reads):
        raise invalid("写字段须为读取字段的子集且不能重复")
    table, can_update = await inspect_table(session, name)
    column = lambda p: p.backing_column or p.source_column
    from app.modules.qa.object_source import primary_property
    primary = primary_property(obj)
    if primary is None or column(primary) not in reads:
        raise invalid("须显式授权读取已发布对象的主键属性")
    if column(primary) not in table.c or not isinstance(table.c[column(primary)].type, String | Integer):
        raise invalid("对象主键须映射到文本或整数列")
    private = {column(p) for p in obj.properties if p.is_sensitive or p.visibility == 'hidden'}
    mapped = {column(p) for p in obj.properties if column(p) and column(p) not in private and not p.transform}
    protected = {column(p) for p in obj.properties if p.is_pk or p.api_name == obj.pk_property or p.is_sensitive or p.visibility == 'hidden'}
    writable = {column(p) for p in obj.properties if column(p) and column(p) not in protected and not p.transform}
    if not set(reads) <= mapped or not set(writes) <= writable or not set(reads) <= set(table.c.keys()):
        raise invalid("字段须对应对象已发布的非敏感属性")
    if set(writes) & {'id', 'code', 'ontology_id', 'lock_version', 'updated_at', 'created_by'}:
        raise invalid("身份和系统字段不能写入")
    if writes and (not can_update or not {'lock_version', 'updated_at'} <= set(table.c.keys())):
        raise invalid("写入需要更新权限及 lock_version、updated_at 字段")
    from app.db.session import get_qa_readonly_engine
    from app.modules.qa.readonly import scope_readonly_conn
    try:
        async with get_qa_readonly_engine().connect() as conn, conn.begin():
            await scope_readonly_conn(conn, obj.ontology_id)
            grants = (await conn.execute(text("""
                SELECT a.attname, has_column_privilege(current_user, c.oid, a.attname, 'SELECT')
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                JOIN pg_attribute a ON a.attrelid=c.oid
                WHERE n.nspname='public' AND c.relname=:name AND a.attnum > 0 AND NOT a.attisdropped
            """), {"name": name})).all()
            granted = {field for field, allowed in grants if allowed}
            if not (set(reads) | {'id', 'ontology_id'}) <= granted:
                raise invalid("问数只读账号缺少所选字段的 SELECT 权限，请管理员先授予后重试")
    except AppError:
        raise
    except Exception:
        raise invalid("无法验证问数只读账号的源表权限，请检查只读连接配置") from None
    return table


async def validate_supplementary(session, obj, main, reads, spec):
    """Validate an explicitly mapped 1:1 table on every save and load."""
    if not isinstance(spec, dict) or set(spec) != {"table", "mainKey", "joinKey", "fields"}:
        raise invalid("补充表配置不完整")
    name, main_key, join_key, fields = (spec[k] for k in ("table", "mainKey", "joinKey", "fields"))
    if not all(isinstance(v, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,62}", v) for v in (name, main_key, join_key)):
        raise invalid("补充表及关联字段名称无效")
    if name == main.name or main_key not in reads or main_key not in main.c:
        raise invalid("主表关联字段须是已授权读取的字段，补充表须不同于主表")
    if not isinstance(fields, dict) or not fields or len(fields) > 100 or not all(isinstance(k, str) and isinstance(v, str) for k, v in fields.items()):
        raise invalid("请选择补充表字段映射")
    table, _ = await inspect_table(session, name)
    if join_key not in table.c or type(main.c[main_key].type) is not type(table.c[join_key].type):
        raise invalid("关联字段不存在或类型不一致")
    # Hold PostgreSQL ACCESS SHARE locks through validation and the caller's read transaction.
    await session.execute(select(main.c[main_key]).limit(0))
    await session.execute(select(table.c[join_key]).limit(0))
    indexes = (await session.execute(text("""
        SELECT array_agg(a.attname ORDER BY k.ord)
        FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid JOIN pg_namespace n ON n.oid=t.relnamespace
        CROSS JOIN LATERAL unnest(i.indkey::smallint[]) WITH ORDINALITY AS k(attnum, ord)
        JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=k.attnum
        WHERE n.nspname='public' AND t.relname=:name AND i.indisunique AND i.indisvalid
          AND i.indisready AND i.indimmediate AND i.indpred IS NULL AND i.indexprs IS NULL
          AND k.ord <= i.indnkeyatts
        GROUP BY i.indexrelid
    """), {"name": name})).scalars().all()
    if not any(set(index) in ({join_key}, {"ontology_id", join_key}) and len(index) == len(set(index)) for index in indexes):
        raise invalid("补充表关联字段缺少数据库唯一约束；一对多请建子对象及关系")
    props = {p.api_name: p for p in obj.properties}
    private = {p.backing_column or p.source_column for p in obj.properties if p.is_sensitive or p.visibility == "hidden"}
    if len(set(fields.values())) != len(fields):
        raise invalid("补充表字段不能重复映射")
    for api, column in fields.items():
        prop = props.get(api)
        if not prop or prop.is_pk or prop.api_name == obj.pk_property or prop.is_sensitive or prop.visibility == "hidden" or prop.transform or (prop.backing_column or prop.source_column) != column or column not in table.c or column in private or column in {"id", "ontology_id"}:
            raise invalid("补充字段须对应已发布对象的公开属性和明确来源列")
    from app.db.session import get_qa_readonly_engine
    from app.modules.qa.readonly import scope_readonly_conn
    try:
        async with get_qa_readonly_engine().connect() as conn, conn.begin():
            await scope_readonly_conn(conn, obj.ontology_id)
            grants = (await conn.execute(text("""
                SELECT a.attname, has_column_privilege(current_user,c.oid,a.attname,'SELECT')
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                JOIN pg_attribute a ON a.attrelid=c.oid
                WHERE n.nspname='public' AND c.relname=:name AND a.attnum>0 AND NOT a.attisdropped
            """), {"name": name})).all()
            if not ({join_key, "ontology_id"} | set(fields.values())) <= {col for col, allowed in grants if allowed}:
                raise invalid("只读账号缺少补充表关联字段或投影字段的 SELECT 权限")
    except AppError:
        raise
    except Exception:
        raise invalid("无法验证补充表只读权限") from None
    return table


async def load_bindings(session, oid):
    from app.common.sql_guard import GovernedTable, InheritVia
    from sqlalchemy.orm import undefer
    has_extra = await session.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid=to_regclass('public.governed_binding') AND attname='supplementary' AND NOT attisdropped)"))
    stmt = select(GovernedBinding).where(GovernedBinding.ontology_id == oid)
    if has_extra:
        stmt = stmt.options(undefer(GovernedBinding.supplementary))
    rows = list(await session.scalars(stmt))
    main_tables = {row.table_name for row in rows}
    result = {}
    blocked = set()
    extras = {}
    invalid_extras = set()
    for row in rows:
        blocked.add(row.object_code)
        spec = row.supplementary if has_extra else None
        obj = await session.scalar(select(ObjectType).where(ObjectType.ontology_id == oid, ObjectType.code == row.object_code))
        if not obj:
            if spec:
                invalid_extras.add(row.object_code)
            continue
        try:
            table = await validate_binding(session, obj, row.table_name, row.read_columns, row.write_columns)
            allowed = set(row.read_columns) | {'id', 'ontology_id'}
            if row.write_columns:
                allowed |= {'lock_version', 'updated_at'}
            conn = await session.connection()
            table = await conn.run_sync(lambda sync: Table(row.table_name, MetaData(), autoload_with=sync, include_columns=allowed, resolve_fks=False))
            main = GovernedTable(table, row.object_code, {key: table.c[key] for key in row.read_columns}, ontology_id=oid, write_columns=frozenset(row.write_columns), projection=frozenset(row.read_columns))
            child = None
            if spec:
                extra = await validate_supplementary(session, obj, table, row.read_columns, spec)
                if extra.name in result or extra.name in main_tables:
                    raise invalid("补充表已绑定其他对象")
                cols = set(spec["fields"].values()) | {spec["joinKey"], "ontology_id"}
                extra = await conn.run_sync(lambda sync: Table(extra.name, MetaData(), autoload_with=sync, include_columns=cols, resolve_fks=False))
                child = GovernedTable(extra, row.object_code, {}, inherit=InheritVia(local=extra.c[spec["joinKey"]], parent=main, parent_key=table.c[spec["mainKey"]]), ontology_id=oid, projection=frozenset(set(spec["fields"].values()) | {spec["joinKey"]}))
                extras[row.object_code] = (spec, child)
            result[row.table_name] = main
            if child is not None:
                result[child.name] = child
        except (AppError, ValueError, TypeError):
            if spec:
                invalid_extras.add(row.object_code)
            continue
    session.info["supplementary_bindings"] = extras
    session.info["supplementary_invalid"] = invalid_extras
    return result, blocked
