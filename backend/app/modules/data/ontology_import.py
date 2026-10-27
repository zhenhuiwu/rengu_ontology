"""只读源结构并生成现有静态 CR；不写源库，不改变查询执行白名单。

草稿生成是通用规则（auto_ontology.heuristic + 本文件的取值提示），不认识任何行业的表名 / 字段名：
- 列名、注释、主外键 → 对象类型、属性、关系（heuristic.build_proposal）；
- 可选的离散取值样本（sampleValues=true）→ 低基数文本列标为维度 + 可筛选，描述里附取值样本；
  两值且像真假的列、名字像时间却是文本的列只给提示，不改类型；
- 大模型默认关闭（generator = "规则"，llm = false）；结果只写成变更单草稿，由静态审核发布。
"""

import hashlib
import json
import re

from fastapi import APIRouter, Depends, Request
from sqlalchemy import or_, select

from app.common.auth import CurrentUser, CurrentUserDep, check_func, require_func
from app.core.errors import AppError, NotFound
from app.db.dialects import get_adapter
from app.db.session import SessionDep
from app.modules.admin.permissions import resolve_owner_name
from app.modules.admin.repo import insert_audit
from app.modules.auto_ontology.heuristic import build_proposal, structural_warnings
from app.modules.data import connector
from app.modules.data.mappings import MAPPING_LOCK, mapped_objects
from app.modules.data.models import Datasource
from app.modules.data.ontology_schemas import DraftIn, ImportPreviewIn, ImportToCrIn, MappingIn
from app.modules.data.secrets import decrypt_password
from app.modules.data.service import DatasourceService, IntrospectFailed, _require_real
from app.modules.static.cr_write import CrWriteService, target_out
from app.modules.static.models import CrItem, LinkType, ObjectType
from app.modules.static.schemas import CRCreateIn, ItemIn, ItemsPutIn, TargetIn

router = APIRouter(tags=["data"])
MANAGE = [Depends(require_func("connections", "manage"))]


def conflict(message: str, code: str = "IMPORT_CONFLICT", **details) -> AppError:
    return AppError(message, code=code, status_code=409, details=details or None)


MAX_ENUM_VALUES = 20  # 去重取值不超过这个数才当维度
SAMPLE_COLUMNS = 40  # 每表最多采样的文本列
BOOL_TOKENS = frozenset({"0", "1", "y", "n", "yes", "no", "true", "false", "t", "f", "是", "否"})
TIME_NAME = re.compile(r"(^|_)(at|time|date|day|dt|ts)$|^(date|time)_", re.IGNORECASE)


def sample_distinct(conn, schema: str, tables) -> dict[str, dict[str, list[str]]]:
    """每张表的文本列（非主键 / 外键）取最多 MAX_ENUM_VALUES + 1 个去重非空值；只读，方言无关。"""
    from sqlalchemy import column, distinct, table

    out: dict[str, dict[str, list[str]]] = {}
    for t in tables:
        fk_cols = {c for f in t.fks for c in f.columns}
        cols = [
            c.name for c in t.columns if c.kind == "string" and c.name not in t.pk and c.name not in fk_cols
        ]
        src = table(t.name, *[column(c) for c in cols[:SAMPLE_COLUMNS]], schema=schema)
        for name in cols[:SAMPLE_COLUMNS]:
            col = src.c[name]
            stmt = select(distinct(col)).where(col.is_not(None)).limit(MAX_ENUM_VALUES + 1)
            out.setdefault(t.name, {})[name] = sorted(str(v) for v in conn.execute(stmt).scalars())
    return out


def value_hints(proposal: dict, tables, samples: dict[str, dict[str, list[str]]]) -> list[str]:
    """按取值样本 / 列名给提议的属性加维度提示（原地修改 proposal），返回人工复核提示。"""
    kinds = {t.name: {c.name: c.kind for c in t.columns} for t in tables}
    hints: list[str] = []
    for obj in proposal["objectTypes"]:
        table_samples = samples.get(obj["table"], {})
        for p in obj["props"]:
            col, kind = p["source"], kinds.get(obj["table"], {}).get(p["source"])
            if kind == "string" and TIME_NAME.search(col):
                hints.append(f"{obj['table']}.{col} 名称像时间但是文本类型，请确认是否需要转换")
            values = table_samples.get(col)
            if values is None or p.get("pk") or p.get("title"):
                continue
            if not values:
                hints.append(f"{obj['table']}.{col} 没有非空取值，请确认是否需要这个属性")
            elif len(values) == 2 and {v.strip().lower() for v in values} <= BOOL_TOKENS:
                hints.append(f"{obj['table']}.{col} 只有两个取值（{'、'.join(values)}），可能是是 / 否标记")
            elif len(values) <= MAX_ENUM_VALUES:
                p["dim"] = True
                p["filterable"] = True
                more = "…" if len(values) > 8 else ""
                sample = f"取值样本：{'、'.join(values[:8])}{more}（共 {len(values)} 个）"
                p["desc"] = f"{p['desc']}；{sample}" if p["desc"] else sample
    return hints


async def inspect_source(session, code: str, body: ImportPreviewIn):
    ds = await DatasourceService(session).get(code)
    _require_real(ds.type)
    if body.schema_ in connector.SYSTEM_SCHEMAS or body.schema_.startswith("pg_"):
        raise AppError("不能导入系统 schema", code="VALIDATION_FAILED", status_code=422)
    if len(set(body.tables)) != len(body.tables):
        raise AppError("表名不能重复", code="VALIDATION_FAILED", status_code=422)
    password = decrypt_password(ds.password_enc)
    if not password:
        raise AppError("请先保存有效的源库密码", code="DS_PASSWORD_REQUIRED", status_code=422)
    cfg = {
        "host": ds.host,
        "port": ds.port,
        "database": ds.database,
        "user": ds.username,
        "password": password,
        "ssl": ds.ssl,
    }
    try:
        found = await connector.inspect_tables(ds.type.value, cfg, body.schema_, body.tables)
    except AppError:
        raise
    except Exception as e:
        raise IntrospectFailed(f"读取源表结构失败：{connector.err_text(e, password)}") from None
    if {t.name for t in found} != set(body.tables):
        raise conflict("源表清单不完整，请刷新后重试", "SCHEMA_CHANGED")
    samples: dict[str, dict[str, list[str]]] = {}
    if body.sample_values:
        try:
            samples = await connector._read(
                ds.type.value, cfg, lambda conn: sample_distinct(conn, body.schema_, found)
            )
        except Exception as e:
            raise IntrospectFailed(f"读取取值样本失败：{connector.err_text(e, password)}") from None
    meta = [dict(t.to_json(), uniqueSets=t.unique_sets) for t in found]
    signature = {
        "ontologyId": ds.ontology_id,
        "datasourceId": ds.code,
        "version": ds.lock_version,
        "schema": body.schema_,
        "tables": meta,
    }
    fingerprint = hashlib.sha256(
        json.dumps(signature, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    proposal = build_proposal(found, ds.name)
    hints = value_hints(proposal, found, samples)
    return (
        ds,
        found,
        {
            "datasourceId": ds.code,
            "datasourceName": ds.name,
            "schema": body.schema_,
            "tables": meta,
            "proposal": proposal,
            "warnings": structural_warnings(found) + hints,
            "fingerprint": fingerprint,
            "generator": "规则",
            "llm": False,
            "sampled": bool(body.sample_values),
        },
    )


async def draft(session, user: CurrentUser, body: DraftIn, first: TargetIn) -> tuple[CrWriteService, str]:
    svc = CrWriteService(session)
    if body.cr_id:
        cr = await svc.lock_draft(body.cr_id, user, body.lock_version)
        # 不能覆盖已有目标的编辑段；导入/映射必须加入空目标。
        return svc, cr.code
    code = await svc.create(
        user, CRCreateIn(title=body.title, reason=body.reason, reviewer=body.reviewer, target=first)
    )
    return svc, code


async def write_target(svc, code, user, target, sections):
    existing = await svc.lock_draft(code, user)
    if await svc.session.scalar(
        select(CrItem.id)
        .where(CrItem.cr_id == existing.id, CrItem.target_kind == target.kind, CrItem.target_id == target.id)
        .limit(1)
    ):
        raise conflict("目标已有编辑内容，请在变更单内继续修改", targetId=target.id)
    await svc.add_target(code, user, target)
    for section, rows in sections.items():
        await svc.put_items(code, user, ItemsPutIn(target=target, section=section, rows=rows))


async def result(svc, code, user, action, request: Request):
    cr = await svc.lock_draft(code, user)
    await insert_audit(
        svc.session,
        actor=f"{user.name}（{user.id}）",
        action=action,
        target=code,
        created_by=user.id,
        ip=(request.client.host if request.client else "")[:64],
    )
    return {
        "crId": code,
        "lockVersion": cr.lock_version,
        "targets": [target_out(t) for t in await svc.targets(cr.id)],
    }


def fields(values, before=None):
    return [
        ItemIn(key=k, op="update" if before is not None else "add", before=(before or {}).get(k), after=v)
        for k, v in values.items()
        if before is None or before.get(k) != v
    ]


@router.post("/datasources/{ds_id}/import-preview", dependencies=MANAGE)
async def preview(ds_id: str, body: ImportPreviewIn, session: SessionDep, user: CurrentUserDep):
    check_func(user, "object-types", "manage")
    _, _, output = await inspect_source(session, ds_id, body)
    return output


@router.post("/object-types/{object_id}/metadata-diff", dependencies=MANAGE)
async def metadata_diff(object_id: str, session: SessionDep, user: CurrentUserDep):
    """重新读取源结构，仅返回与已发布属性映射的差异，不修改定义或草稿。"""
    check_func(user, "object-types", "manage")
    obj = await session.scalar(select(ObjectType).where(ObjectType.code == object_id))
    if not obj:
        raise NotFound("对象类型不存在")
    if not obj.datasource_name or not obj.source_table:
        raise conflict("对象尚未配置数据源和源表映射", "MAPPING_REQUIRED")
    sources = list(
        await session.scalars(
            select(Datasource).where(
                or_(Datasource.code == obj.datasource_name, Datasource.name == obj.datasource_name)
            )
        )
    )
    if len(sources) != 1:
        raise conflict("数据连接不存在或名称不唯一，请先修正对象映射", "MAPPING_REQUIRED")
    schema, table = (
        obj.source_table.rsplit(".", 1) if "." in obj.source_table else ("public", obj.source_table)
    )
    _, found, _ = await inspect_source(
        session, sources[0].code, ImportPreviewIn(schema=schema, tables=[table])
    )
    mapped = {p.map_column or p.source_column for p in obj.properties if p.map_column or p.source_column}
    columns = found[0].to_json()["columns"]
    names = {c["name"] for c in columns}
    return {
        "objectId": obj.code,
        "datasourceId": sources[0].code,
        "schema": schema,
        "table": table,
        "added": [c for c in columns if c["name"] not in mapped],
        "deleted": sorted(mapped - names),
    }


@router.post("/datasources/{ds_id}/import-to-cr", status_code=201, dependencies=MANAGE)
async def apply_import(
    ds_id: str, body: ImportToCrIn, session: SessionDep, user: CurrentUserDep, request: Request
):
    check_func(user, "object-types", "manage")
    if body.link_ids or (body.link_ids is None and body.include_links):
        check_func(user, "link-types", "manage")
    await DatasourceService(session).get(ds_id)  # 404 一致；随后锁父行避免内省期间被删除/改配置。
    await session.scalar(select(Datasource).where(Datasource.code == ds_id).with_for_update())
    ds, _found, preview = await inspect_source(session, ds_id, body)
    if body.fingerprint != preview["fingerprint"]:
        raise conflict("源结构或连接参数已变化，请重新预览", "SCHEMA_CHANGED")
    proposal = preview["proposal"]
    links = {link["id"]: link for link in proposal["linkTypes"]}
    selected = body.link_ids if body.link_ids is not None else list(links) if body.include_links else []
    if len(set(selected)) != len(selected) or set(selected) - links.keys():
        raise conflict("关系选择必须来自本次预览，且不能重复")
    by_table = {o.table: o for o in body.object_types}
    if len(by_table) != len(body.object_types) or set(by_table) != {
        o["table"] for o in proposal["objectTypes"]
    }:
        raise conflict("对象清单必须与预览生成的对象一一对应")
    ids, apis = [o.id for o in body.object_types], [o.api for o in body.object_types]
    if len(set(ids)) != len(ids) or len(set(apis)) != len(apis):
        raise conflict("对象编号或 API 名重复")
    await get_adapter(session).xact_lock(session, MAPPING_LOCK)
    if await session.scalar(
        select(ObjectType.id).where(or_(ObjectType.code.in_(ids), ObjectType.api_name.in_(apis))).limit(1)
    ):
        raise conflict("对象编号或 API 名已存在，请修改后重试")
    from app.modules.static.cr_write import HOLDING
    from app.modules.static.models import ChangeRequest

    reserved = await session.scalars(
        select(CrItem)
        .join(ChangeRequest, ChangeRequest.id == CrItem.cr_id)
        .where(
            ChangeRequest.status.in_(HOLDING),
            CrItem.target_kind == "object",
            CrItem.item_key == "api",
            CrItem.section.in_(["def", "config"]),
            CrItem.op != "delete",
        )
    )
    if any(i.after in apis for i in reserved):
        raise conflict("API 名已被未发布的变更单使用，请修改后重试")
    names = {}
    for o in body.object_types:
        owner = o.owner or user.id
        try:
            await resolve_owner_name(session, owner)
        except AppError as e:
            raise conflict(e.message, owner=owner) from None
        names[o.table] = owner  # 草稿保留稳定编号，发布时重新校验并存真实姓名。
    plans = []
    for obj in proposal["objectTypes"]:
        chosen = by_table[obj["table"]]
        qualified = f"{body.schema_}.{obj['table']}"
        if len(qualified) > 128:
            raise conflict("schema 与表名组合超过对象映射长度限制", table=obj["table"])
        if len(obj.get("pk") or "") > 64 or any(
            len(p["api"]) > 64 or len(p["name"]) > 64 for p in obj["props"]
        ):
            raise conflict("源字段名超过对象属性长度限制", table=obj["table"])
        if not obj["pk"]:
            raise conflict("导入对象需要单列主键，复合主键/无主键请先人工建模", table=obj["table"])
        definition = {
            "name": chosen.name,
            "api": chosen.api,
            "group": obj["group"],
            "table": qualified,
            "source": ds.code,
            "pk": obj["pk"],
            "desc": obj["desc"],
            "owner": names[obj["table"]],
        }
        props = [ItemIn(key=p["api"], op="add", after=p) for p in obj["props"]]
        plans.append(
            (
                TargetIn(kind="object", id=chosen.id, name=chosen.name, is_new=True),
                {"def": fields(definition), "properties": props},
            )
        )
    if selected:
        for link in (links[key] for key in selected):
            if len(link["id"]) > 64 or len(link["name"]) > 64:
                raise conflict("生成的关系编号或名称过长，请减少表名长度或取消生成关系")
            if await session.scalar(select(LinkType.id).where(LinkType.code == link["id"])):
                raise conflict("关系编号已存在", id=link["id"])
            values = {
                k: link[k] for k in ("name", "card", "fromProp", "toProp", "reverse", "nullable", "desc")
            }
            values.update({"from": by_table[link["from"]].id, "to": by_table[link["to"]].id})
            if link.get("via"):
                values["via"] = f"{body.schema_}.{link['via']}"
            if len(values.get("via", "")) > 64 or len(values["reverse"]) > 64:
                raise conflict("生成的中间表名或反向关系名超过长度限制")
            plans.append(
                (
                    TargetIn(kind="link", id=link["id"], name=link["name"], is_new=True),
                    {"def": fields(values)},
                )
            )
    svc, code = await draft(session, user, body, plans[0][0])
    for target, sections in plans:
        await write_target(svc, code, user, target, sections)
    return await result(svc, code, user, "从源表导入本体定义草稿", request)


@router.get("/datasources/{ds_id}/mappings", dependencies=[Depends(require_func("connections", "view"))])
async def mappings(ds_id: str, session: SessionDep):
    ds = await DatasourceService(session).get(ds_id)
    return [
        {
            "objectId": o.code,
            "name": o.display_name,
            "schema": (o.source_table or "").rsplit(".", 1)[0] if "." in (o.source_table or "") else "public",
            "table": (o.source_table or "").rsplit(".", 1)[-1],
            "datasourceName": ds.name,
        }
        for o in await mapped_objects(session, ds)
    ]


@router.put("/datasources/{ds_id}/tables/{schema}/{table}/mapping", status_code=201, dependencies=MANAGE)
async def edit_mapping(
    ds_id: str,
    schema: str,
    table: str,
    body: MappingIn,
    session: SessionDep,
    user: CurrentUserDep,
    request: Request,
):
    check_func(user, "object-types", "manage")
    await DatasourceService(session).get(ds_id)
    await session.scalar(select(Datasource).where(Datasource.code == ds_id).with_for_update())
    ds, found, _ = await inspect_source(session, ds_id, ImportPreviewIn(schema=schema, tables=[table]))
    await get_adapter(session).xact_lock(session, MAPPING_LOCK)
    obj = await session.scalar(select(ObjectType).where(ObjectType.code == body.object_id))
    if obj is None:
        raise NotFound("对象类型不存在")
    columns = {c.name for c in found[0].columns}
    missing = [p.source_column for p in obj.properties if p.source_column and p.source_column not in columns]
    pk = next((p.source_column for p in obj.properties if p.api_name == obj.pk_property), None)
    if not pk or pk not in columns or missing:
        raise conflict("新表缺少已映射字段，需先在对象变更单调整属性", missing=missing)
    qualified = f"{schema}.{table}"
    if len(qualified) > 128:
        raise conflict("schema 与表名组合超过对象映射长度限制")
    before = {"source": obj.datasource_name, "table": obj.source_table}
    rows = fields({"source": ds.code, "table": qualified}, before)
    if not rows:
        raise conflict("对象已映射到该表，无需创建变更单")
    target = TargetIn(kind="object", id=obj.code, name=obj.display_name)
    svc, code = await draft(session, user, body, target)
    await write_target(svc, code, user, target, {"config": rows})
    return await result(svc, code, user, "修改对象数据源映射草稿", request)
