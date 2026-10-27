"""Bounded authorized primary-key samples through the existing object read adapter."""

from collections import Counter
from types import SimpleNamespace

from app.common.ontology import require_ontology_id
from app.core.errors import AppError
from app.modules.qa.explore_api import EXPORT_MAX, Bound, _bind
from app.modules.qa.object_source import configured_bindings, primary_property, resolve_source

SAMPLE_LIMIT = EXPORT_MAX


def sample_result(values, *, truncated=False):
    if not values:
        return "警告", "授权样本为空，不能判断主键唯一性；未认证全源数据"
    missing = sum(value is None or isinstance(value, str) and not value.strip() for value in values)
    counts = Counter(value for value in values if value is not None)
    duplicates = sum(count > 1 for count in counts.values())
    extent = f"当前授权样本{len(values)}条" + (f"（达到{SAMPLE_LIMIT}条上限）" if truncated else "")
    if missing or duplicates:
        return "阻断", f"{extent}发现{missing}条空主键、{duplicates}组重复主键；未输出原值，不能作为唯一标识"
    return "通过", f"{extent}内未发现空值或重复；仅当前授权样本，不代表全源唯一"


async def sample_primary_key(session, code, current, values, properties, user):
    if user is None:
        return "不适用", "缺少获准读取身份，未执行主键数据抽检"
    if user.ontology_id != require_ontology_id(session):
        return "不适用", "读取身份与当前本体不一致，未执行主键数据抽检"
    current_props = {prop.api_name: prop for prop in current.properties} if current is not None else {}
    props = [
        SimpleNamespace(
            api_name=api,
            data_type=row.get("type", "string"),
            source_column=row.get("source") or "",
            transform=getattr(current_props.get(api), "transform", None),
            is_pk=bool(row.get("pk")),
            is_title=bool(row.get("title")),
            is_sensitive=bool(row.get("sensitive")),
            visibility=row.get("visibility") or "normal",
        )
        for api, row in properties.items()
    ]
    projected = SimpleNamespace(
        code=code,
        properties=props,
        pk_property=values.get("pk"),
        datasource_name=values.get("source"),
        source_table=values.get("table"),
    )
    primary = primary_property(projected)
    if primary is None or primary.is_sensitive or primary.visibility == "hidden":
        return "不适用", "本单主键尚无公开可读取映射，未执行数据抽检"
    try:
        # The savepoint contains SELECT errors, not temporary definition/publication writes.
        async with session.begin_nested():
            source = await resolve_source(session, projected)
            if source is not None:
                bound = Bound(
                    projected,
                    {"table": source.table.name, "id": source.id_column, "columns": source.columns},
                    source,
                )
            else:
                explicit = any(
                    row["ontologyId"] == user.ontology_id and row["objectType"] == code
                    for row in configured_bindings()
                )
                unchanged = (
                    current is not None
                    and values.get("pk") == current.pk_property
                    and values.get("source") == current.datasource_name
                    and values.get("table") == current.source_table
                )
                unchanged = unchanged and {p.api_name: p.source_column for p in current.properties} == {
                    api: row.get("source") or "" for api, row in properties.items()
                }
                if explicit or not unchanged:
                    return "不适用", "缺少支持本单主键映射的受控来源，未借用旧主键执行抽检"
                _, bound = await _bind(session, code)
            if bound is None:
                return "不适用", "未配置受控来源，未执行主键数据抽检"
            stmt = (
                (await bound.stmt(user))
                .with_only_columns(bound.id_col, maintain_column_froms=True)
                .limit(SAMPLE_LIMIT + 1)
            )
            # No candidate-key ORDER BY: an unindexed key must not trigger a full source sort.
            async with bound.read(session) as reader:
                keys = (await reader.execute(stmt)).scalars().all()
            return sample_result(keys[:SAMPLE_LIMIT], truncated=len(keys) > SAMPLE_LIMIT)
    except AppError:
        return "不适用", "当前权限、主键公开映射或受控来源不允许抽检；未判断唯一性"
    except Exception:
        return "警告", "主键抽检未完成，请检查受控来源；未判断唯一性，未返回源错误细节"
