"""Read-only dimension phrase matching against published definitions or one saved CR target."""

from copy import deepcopy
from typing import Any

from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser, check_func, resolve_user
from app.common.context import ViewContext
from app.core.errors import AppError, EmptyInput, NotFound
from app.core.schema import ApiModel
from app.modules.static import history
from app.modules.static.models import ChangeRequest, CrItem, CRStatus, CrTarget
from app.modules.static.permissions import check_cr
from app.modules.static.service import StaticService


class DimensionMatchIn(ApiModel):
    text: str = Field(default="", max_length=512)
    dimension: str | None = Field(default=None, min_length=1, max_length=64)
    all_dimensions: bool = Field(default=False, alias="all")
    cr_id: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("text", "dimension", "cr_id", mode="before")
    @classmethod
    def valid_text(cls, value: Any, info):
        if value is None and info.field_name == "text":
            return ""
        if isinstance(value, str):
            if "\x00" in value:
                raise ValueError("文本不能包含空字符")
            try:
                value.encode("utf-8")
            except UnicodeError:
                raise ValueError("文本含无效Unicode字符") from None
            return value.strip()
        return value


def invalid(field: str, message: str) -> AppError:
    return AppError(
        "参数校验失败",
        code="VALIDATION_FAILED",
        status_code=422,
        details={"fields": [{"field": field, "message": message}]},
    )


def valid_context(ctx: ViewContext) -> None:
    for field, value in (("ver", ctx.ver), ("previewAs", ctx.preview_as)):
        if value is None:
            continue
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise invalid(field, "文本含无效Unicode字符") from None
        if "\x00" in value or len(value) > 64:
            raise invalid(field, "编号过长或包含空字符")


def apply_rows(dimension: dict, rows: list[CrItem]) -> None:
    """Apply only fields used by the existing matcher; CR payload cannot replace runtime hit counts."""
    values = {row["key"]: dict(row) for row in dimension["values"]}
    mappings = {row["key"]: dict(row) for row in dimension["mappings"]}
    for item in rows:
        if item.section in ("def", "config") and item.item_key == "name" and item.op != "delete":
            if not isinstance(item.after, str) or not item.after.strip():
                raise invalid("crId", "维度草稿名称不合法")
            dimension["name"] = item.after
            continue
        if item.section not in ("values", "mappings"):
            continue
        bucket = values if item.section == "values" else mappings
        if item.op == "delete":
            bucket.pop(item.item_key, None)
            continue
        if not isinstance(item.after, dict):
            raise invalid("crId", "取值或映射草稿必须是对象")
        previous = bucket.get(item.item_key)
        if item.op != "add" and previous is None:
            raise invalid("crId", "修改的取值或映射不在已发布基线中")
        if item.section == "values":
            allowed = ("code", "name", "level", "parent", "synonyms", "status", "note")
            row = {"key": item.item_key, "synonyms": [], "status": "启用", **(previous or {})}
            row.update({key: item.after[key] for key in allowed if key in item.after})
            if (
                not isinstance(row.get("name"), str)
                or not row["name"].strip()
                or row["status"] not in ("启用", "停用")
                or not isinstance(row["synonyms"], list)
                or any(not isinstance(word, str) for word in row["synonyms"])
            ):
                raise invalid("crId", "取值草稿的名称、状态或同义词不合法")
        else:
            allowed = ("phrase", "targets", "match", "resolve")
            row = {
                "key": item.item_key,
                "targets": [],
                "source": "业务知识",
                "hits": None,
                **(previous or {}),
            }
            row.update({key: item.after[key] for key in allowed if key in item.after})
            if (
                not isinstance(row.get("phrase"), str)
                or not row["phrase"].strip()
                or not isinstance(row["targets"], list)
                or any(not isinstance(target, str) for target in row["targets"])
            ):
                raise invalid("crId", "映射草稿的说法或目标不合法")
        for flag in ("draft", "diff", "pub"):
            row.pop(flag, None)
        bucket[item.item_key] = row
    dimension["values"], dimension["mappings"] = list(values.values()), list(mappings.values())


async def overlay_target(
    session: AsyncSession, dims: list[dict], body: DimensionMatchIn, user: CurrentUser, effective: CurrentUser
) -> None:
    cr = await session.scalar(select(ChangeRequest).where(ChangeRequest.code == body.cr_id))
    if cr is None:
        raise NotFound("变更单不存在")
    await check_cr(session, cr, user)
    if effective.id != user.id:
        await check_cr(session, cr, effective)
    if cr.status == CRStatus.published:
        raise AppError("已发布变更单不能叠加到当前试算", code="STATE_INVALID", status_code=409)
    target = await session.scalar(
        select(CrTarget).where(
            CrTarget.cr_id == cr.id,
            CrTarget.target_kind == "dimension",
            CrTarget.target_id == body.dimension,
        )
    )
    if target is None:
        raise NotFound("变更单未登记该维度")
    dimension = next((d for d in dims if d["id"] == body.dimension), None)
    if dimension is None:
        if not target.is_new:
            raise NotFound("维度没有已发布基线，且目标未标记为新建")
        dimension = {
            "id": target.target_id,
            "name": target.target_name or target.target_id,
            "values": [],
            "mappings": [],
        }
        dims.append(dimension)
    rows = list(
        await session.scalars(
            select(CrItem)
            .where(
                CrItem.cr_id == cr.id,
                CrItem.target_kind == "dimension",
                CrItem.target_id == body.dimension,
            )
            .order_by(CrItem.seq, CrItem.id)
        )
    )
    apply_rows(dimension, rows)


async def match_dimension(
    session: AsyncSession, ctx: ViewContext, user: CurrentUser, body: DimensionMatchIn
) -> dict:
    valid_context(ctx)
    effective = await resolve_user(session, ctx.preview_as) if ctx.preview_as else user
    check_func(effective, "dimensions", "view")
    if not body.text:
        raise EmptyInput("请输入需要匹配的说法")
    if (not body.all_dimensions or body.cr_id) and not body.dimension:
        raise invalid("dimension", "本维度或草稿试算必须指定维度")
    if ctx.ver and body.cr_id:
        raise invalid("crId", "历史版本不能叠加当前变更单")
    svc = StaticService(session, ctx)
    dims = [history.dimension_at(deepcopy(d), ctx.ver) for d in await svc._dims() if d["status"] == "已发布"]
    if body.cr_id:
        await overlay_target(session, dims, body, user, effective)
    selected = next((d for d in dims if d["id"] == body.dimension), None)
    if body.dimension and selected is None:
        raise NotFound("维度不存在于当前试算定义中")
    phrases = svc._phrases(dims)

    def in_scope(hit):
        return body.all_dimensions or hit["item"] == body.dimension

    exact = [hit for hit in phrases.get(body.text, []) if in_scope(hit)]
    seen = {(hit["item"], hit["value"]) for hit in exact}
    contains = []
    if len(body.text) >= 2:
        for phrase, hits in phrases.items():
            if phrase == body.text or body.text not in phrase:
                continue
            for hit in hits:
                identity = (hit["item"], hit["value"])
                if in_scope(hit) and identity not in seen:
                    contains.append(hit)
                    seen.add(identity)
    scope = dims if body.all_dimensions else [selected]
    priority = ([selected] if selected else []) + scope
    rule = next((m for d in priority for m in d["mappings"] if m["phrase"] == body.text), None)
    grouped = {}
    for hit in [*exact, *contains]:
        group = grouped.setdefault(hit["item"], {"name": hit["itemName"], "values": []})
        if hit["value"] not in group["values"]:
            group["values"].append(hit["value"])
    groups = []
    for _, group in sorted(grouped.items(), key=lambda pair: pair[0] != "org"):
        values = group["values"]
        groups.append(
            f"{group['name']} › {'、'.join(values[:2])}"
            + (f" 等 {len(values)} 个" if len(values) > 2 else "")
        )
    return {
        "exact": exact,
        "contains": contains[:6],
        "more": max(0, len(contains) - 6),
        "rule": rule,
        "groups": groups,
        "crossDim": body.all_dimensions and len(grouped) > 1,
    }
