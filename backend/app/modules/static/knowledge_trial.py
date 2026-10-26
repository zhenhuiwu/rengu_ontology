"""业务知识词义预览：只匹配已存定义，不执行查询或生成业务答案。"""

from copy import deepcopy
from datetime import date, timedelta

from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser, check_func, resolve_user
from app.common.context import ViewContext
from app.core import clock
from app.core.errors import AppError, EmptyInput, NotFound
from app.core.schema import ApiModel
from app.modules.static import shapes
from app.modules.static.models import ChangeRequest, MetricStatus
from app.modules.static.permissions import check_cr
from app.modules.static.repo import definition_draft_items
from app.modules.static.service import StaticService
from app.modules.versioning.repo import ReleaseRepo


class KnowledgeResolveIn(ApiModel):
    text: str = Field(default="", max_length=512)
    knowledge: str = Field(min_length=1, max_length=64)
    cr_id: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("text", "knowledge", "cr_id")
    @classmethod
    def valid_text(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if "\x00" in value or any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError("文本含无效字符")
        return value.strip()


def time_hint(text: str, terms: list[dict]) -> str:
    periods = {
        (p["start"], p["end"], p["name"])
        for term in terms
        for p in term.get("periods") or []
        if isinstance(p, dict) and p.get("name") and p["name"] in text and p.get("start") and p.get("end")
    }
    if periods:
        return "；".join(f"{start} 至 {end}（{name}）" for start, end, name in sorted(periods))
    today = clock.today()
    if "昨天" in text:
        return str(today - timedelta(days=1))
    monday = today - timedelta(days=today.weekday())
    if "上周" in text:
        return f"{monday - timedelta(days=7)} 至 {monday - timedelta(days=1)}"
    if "本周" in text:
        return f"{monday} 至 {today}"
    if "上个月" in text or "上月" in text:
        end = today.replace(day=1) - timedelta(days=1)
        return f"{end.replace(day=1)} 至 {end}"
    if "本月" in text:
        return f"{today.replace(day=1)} 至 {today}"
    if "今年" in text:
        return f"{date(today.year, 1, 1)} 至 {today}"
    if "近 30 天" in text or "近30天" in text:
        return f"{today - timedelta(days=29)} 至 {today}"
    return "未识别到时间范围，请明确日期或选用已配置日历区间"


def preview_rows(
    text: str, selected: str, terms: list[dict], metrics: list[dict], dims: list[dict]
) -> list[dict]:
    words: dict[str, list[dict]] = {}
    for term in terms:
        for word in [term["name"], *(term.get("synonyms") or [])]:
            if word and word in text:
                hits = words.setdefault(word, [])
                if not any(hit["id"] == term["id"] for hit in hits):
                    hits.append(term)
    rows = []
    hit_ids = {term["id"] for hits in words.values() for term in hits}
    for word, hits in words.items():
        if len(hits) > 1:
            rows.append(
                {
                    "k": f"“{word}”",
                    "v": "同义词冲突：" + " / ".join(t["name"] for t in hits) + "，需要明确所指知识",
                }
            )
        else:
            term = hits[0]
            expression = term.get("calc") or term.get("maps") or "未配置表达式"
            rows.append(
                {
                    "k": f"“{word}”",
                    "v": f"{term['name']}（{term['type']}）→ {expression}",
                    "by": term["id"],
                }
            )
    # 冲突知识不产生默认过滤或指标选择，不能擅自采用其中一条。
    unambiguous = {hits[0]["id"] for hits in words.values() if len(hits) == 1}
    ambiguous = {term["id"] for hits in words.values() if len(hits) > 1 for term in hits}
    active = [term for term in terms if term["id"] in unambiguous - ambiguous]
    applied = {a["id"] for term in active for a in term.get("applies") or [] if a.get("kind") == "metric"}
    matched = [
        m
        for m in metrics
        if m["id"] in applied or any(w and w in text for w in (m.get("name"), m.get("api")))
    ]
    rows.append(
        {
            "k": "指标",
            "v": "、".join(f"{m['name']} {m.get('version') or ''}".strip() for m in matched)
            or "未识别到指标，请明确所需指标",
            **({} if matched else {"cls": "c-3"}),
        }
    )
    rows.append({"k": "时间", "v": time_hint(text, terms)})
    groups = list(
        dict.fromkeys(
            lv["name"]
            for dim in dims
            for lv in dim.get("levels") or []
            if lv.get("name") and "各" + lv["name"] in text
        )
    )
    if groups:
        rows.append({"k": "分组", "v": "、".join("按" + group for group in groups)})
    filters = list(
        dict.fromkeys(
            f"{f['field']} {f['cond']}"
            for term in active
            if not term.get("calcChanged")
            for f in term.get("filters") or []
            if f.get("field") and f.get("cond")
        )
    )
    rows.append({"k": "默认过滤", "v": "；".join(filters) or "无", **({} if filters else {"cls": "c-3"})})
    if any(term.get("calcChanged") for term in active):
        rows.append(
            {
                "k": "草稿过滤",
                "v": "计算方式已修改，原过滤未用于本次预览；需在发布前核对过滤定义",
                "cls": "c-3",
            }
        )
    if selected not in hit_ids:
        rows.append({"k": "本条知识", "v": "问题未匹配本条已存知识名称或同义词", "cls": "c-3"})
    return rows


async def resolve_knowledge(
    session: AsyncSession, ctx: ViewContext, user: CurrentUser, body: KnowledgeResolveIn
) -> dict:
    effective = await resolve_user(session, ctx.preview_as) if ctx.preview_as else user
    check_func(effective, "glossary", "view")
    if not body.text:
        raise EmptyInput("请输入试用问题")
    if ctx.ver and body.cr_id:
        raise AppError("历史版本不能叠加当前草稿", code="VALIDATION_FAILED", status_code=422)
    svc = StaticService(session, ctx)
    if ctx.ver:
        release = await ReleaseRepo(session).by_ver_or_404(ctx.ver)
        snapshot = release.snapshot or {}
        terms = deepcopy(list((snapshot.get("terms") or {}).values()))
        metrics = list((snapshot.get("metrics") or {}).values())
        dims = list((snapshot.get("dimensions") or {}).values())
    else:
        terms = deepcopy(await svc._terms())
        metrics = [
            shapes.metric_list_item(m) for m in await svc.repo.metrics() if m.status == MetricStatus.published
        ]
        dims = await svc._dims()
    selected = next((t for t in terms if t["id"] == body.knowledge), None)
    if body.cr_id:
        cr = await session.scalar(select(ChangeRequest).where(ChangeRequest.code == body.cr_id))
        if cr is None:
            raise NotFound("变更单不存在")
        await check_cr(session, cr, user)
        await check_cr(session, cr, effective)
        items = await definition_draft_items(session, body.cr_id, "knowledge", body.knowledge)
        if selected is None:
            selected = {
                "id": body.knowledge,
                "name": body.knowledge,
                "type": "术语",
                "synonyms": [],
                "status": "草稿",
            }
            terms.append(selected)
        for item in items:
            key = item["key"]
            if item["op"] == "delete":
                continue  # content/status 写回约定：删除差异行表示不覆盖发布值。
            if item["section"] == "content" and key in {"name", "type", "synonyms", "calc", "applies"}:
                value = item["after"]
                valid = (
                    isinstance(value, list)
                    and len(value) <= 128
                    and all(
                        isinstance(x, str)
                        if key == "synonyms"
                        else isinstance(x, dict) and isinstance(x.get("id"), str)
                        for x in value
                    )
                    if key in {"synonyms", "applies"}
                    else isinstance(value, str)
                )
                if not valid:
                    raise AppError("知识草稿字段格式无效", code="VALIDATION_FAILED", status_code=422)
                selected[key] = value
                if key == "calc":
                    selected["calcChanged"] = True
            elif item["section"] == "status" and key == "status":
                selected["status"] = item["after"]
    if selected is None:
        raise NotFound("业务知识不在当前版本中")
    terms = [
        t
        for t in terms
        if t.get("status") == "已发布" or (body.cr_id and t is selected and t.get("status") != "已停用")
    ]
    dims = [dim for dim in dims if dim.get("status") == "已发布"]
    return {"rows": preview_rows(body.text, body.knowledge, terms, metrics, dims)}
