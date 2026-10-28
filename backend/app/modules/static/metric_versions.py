"""指标真实版本账本与口径回滚：已有JSON/发布快照存储，无新表。"""

import json
import re
from copy import deepcopy
from typing import Annotated, Any

from pydantic import Field, StringConstraints, model_validator
from sqlalchemy import select

from app.common.auth import CurrentUser, check_func
from app.core.errors import AppError, NotFound
from app.core.schema import ApiModel, _fmt_dt
from app.modules.static.cr_write import CrWriteService, item_out
from app.modules.static.models import CrItem, Metric
from app.modules.static.schemas import CRCreateIn, ItemIn, ItemsPutIn, TargetIn
from app.modules.versioning.models import OntologyRelease

FIELDS = ("name", "api", "group", "unit", "owner", "formula", "dims", "variants", "status")
VERSION = re.compile(r"^v([1-9][0-9]{0,14})$")
Obj = dict[str, Any]
# 执行口径（def_rows 里的 k/v 行）可经变更单 metric/def 写：项 key → 行标签。
EXEC_KEYS = {"timeField": "时间字段", "defaultFilter": "默认过滤", "defaultRange": "默认时间范围"}
EXEC_MAX = 256
# 问数元数据（同样是 def_rows 的 k/v 行，可经 metric/def 写、随快照保存与回滚；不影响执行口径与版本号）。
META_KEYS = {"synonyms": "同义词", "desc": "说明"}
SYN_SPLIT = re.compile(r"[、,，;；\n]+")
SYN_MAX, SYN_WORD_MAX, DESC_MAX = 32, 64, 1000


def exec_def(rows: Any) -> Obj:
    """def_rows 中已保存的执行口径 {timeField, defaultFilter}；没有的键不出现，不推断默认值。"""
    labels = {v: k for k, v in EXEC_KEYS.items()}
    values = {
        labels[r["k"]]: r["v"]
        for r in rows or []
        if isinstance(r, dict)
        and r.get("k") in labels
        and (isinstance(r.get("v"), str) or r.get("k") == EXEC_KEYS["defaultRange"])
    }
    # New snapshots distinguish explicitly unconfigured defaults from old unknown snapshots.
    values.setdefault("defaultRange", None)
    return values


def split_words(text: Any) -> list[str]:
    """同义词行文本（「营业额、销售收入」）→ 去空去重的词列表。"""
    out: list[str] = []
    for w in SYN_SPLIT.split(text) if isinstance(text, str) else []:
        if w.strip() and w.strip() not in out:
            out.append(w.strip())
    return out


def meta_def(rows: Any) -> Obj:
    """def_rows 中的问数元数据：{synonyms: [词], desc: 文本}；没有该行时为 [] / ""。"""
    got = {r["k"]: r["v"] for r in rows or [] if isinstance(r, dict) and isinstance(r.get("v"), str)}
    return {"synonyms": split_words(got.get(META_KEYS["synonyms"])), "desc": got.get(META_KEYS["desc"], "")}


def with_meta(rows: Any, key: str, value: Any) -> list[Any]:
    """返回新的 def_rows：写同义词（词列表，存成「、」连接的行）或说明；空值删除该行。"""
    text = "、".join(value) if key == "synonyms" and isinstance(value, list) else value
    return _with_row(rows, META_KEYS[key], text or None)


def with_exec(rows: Any, key: str, value: Any) -> list[Any]:
    """返回新的 def_rows：原位替换（或追加）该执行口径行；value=None 删除该行，其他展示行原样保留。"""
    return _with_row(rows, EXEC_KEYS[key], value)


def _with_row(rows: Any, label: str, value: Any) -> list[Any]:
    out, done = [], False
    for r in rows or []:
        if isinstance(r, dict) and r.get("k") == label:
            if value is not None and not done:
                out.append({**r, "v": value})
            done = True
            continue
        out.append(deepcopy(r))
    if value is not None and not done:
        out.append({"k": label, "v": value})
    return out


def seed_def_rows(rows: Any, *, own: bool) -> list[Any]:
    """种子指标共用 METRIC_DEF.json（METRIC_LIST 首项的示例定义）：同义词/说明只属于首项。"""
    labels = set(META_KEYS.values())
    return [deepcopy(r) for r in rows or [] if own or not (isinstance(r, dict) and r.get("k") in labels)]


def conflict(message: str, code: str = "METRIC_VERSION_INVALID", **details) -> AppError:
    return AppError(message, code=code, status_code=409, details=details or None)


def definition(metric: Metric) -> Obj:
    return {
        "name": metric.display_name,
        "api": metric.api_name,
        "group": metric.metric_group,
        "unit": metric.unit,
        "owner": metric.owner,
        "formula": metric.formula,
        "dims": deepcopy(metric.dims or []),
        "variants": [
            {k: v[k] for k in ("code", "label", "default") if k in v}
            for v in metric.variants or []
            if isinstance(v, dict)
        ],
        "status": metric.status.value,
        "execDef": exec_def(metric.def_rows),
    }


def real_rows(rows: list[Any]) -> list[Obj]:
    return [
        deepcopy(row)
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("definition"), dict)
        and all(field in row["definition"] for field in FIELDS)
    ]


async def ledger(session, code: str, metric: Metric | None = None) -> list[Obj]:
    """跨整版回滚/物理删除仍可从已有发布快照找回真实账本，种子说明行不推导定义。"""
    current = (
        metric if metric is not None else await session.scalar(select(Metric).where(Metric.code == code))
    )
    rows = deepcopy(current.version_rows or []) if current is not None else []
    snapshots = await session.scalars(
        select(OntologyRelease.snapshot["metrics"][code]["versionRows"]).order_by(OntologyRelease.id)
    )
    for stored in snapshots:
        if isinstance(stored, list):
            rows.extend(real_rows(stored))
    seen = set()
    output = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        signature = json.dumps(row, ensure_ascii=False, sort_keys=True)
        if signature not in seen:
            seen.add(signature)
            output.append(row)
    return output


def next_version(current: str | None, rows: list[Obj], requested: Any = None) -> str:
    nums = [
        int(match[1])
        for value in [current, *(row.get("ver") for row in rows)]
        if isinstance(value, str) and (match := VERSION.fullmatch(value))
    ]
    maximum = max(nums, default=0)
    if requested is not None and requested != current:
        if not isinstance(requested, str) or not (match := VERSION.fullmatch(requested)):
            raise conflict("口径版本须为v加正整数，且不超过16字")
        if int(match[1]) <= maximum:
            raise conflict("新口径版本须高于已有全部历史版本", maximum=f"v{maximum}")
        return requested
    result = f"v{maximum + 1}"
    if len(result) > 16:
        raise conflict("口径版本超过当前模型长度限制")
    return result


async def record_publish(
    ctx, metric: Metric, before: Obj | None, previous: str | None, requested: Any
) -> None:
    """一次metric/def钩子调用对应同发布同目标一次记录；无实际变更不虚增版本。"""
    after = definition(metric)
    rows = await ledger(ctx.session, metric.code, metric)
    if before == after and (requested is None or requested == previous):
        return
    from app.modules.admin.models import SysUser

    by = await ctx.session.scalar(select(SysUser.name).where(SysUser.code == ctx.by))
    if by is None:
        raise conflict("发布人不存在，不能记录口径版本")
    actual = real_rows(rows)
    # 仅补亲眼读到的当前已发布定义基线；不猜旧v1/v2，也不冒充旧版的发布时间。
    if before is not None and previous and not any(row.get("ver") == previous for row in actual):
        rows.insert(
            0,
            {
                "ver": previous,
                "release": "",
                "by": by,
                "at": _fmt_dt(ctx.now),
                "change": "本次变更前定义基线（记录时间，非原发布时间）",
                "baseline": True,
                "cr": ctx.cr.code,
                "definition": before,
            },
        )
    version = next_version(previous, rows, requested)
    metric.version = version
    metric.version_rows = [
        {
            "ver": version,
            "release": ctx.ver,
            "by": by,
            "at": _fmt_dt(ctx.now),
            "change": ctx.cr.reason or ctx.cr.title,
            "cr": ctx.cr.code,
            "definition": after,
        },
        *rows,
    ]


async def output_versions(session, metric: Metric) -> list[Obj]:
    rows = await ledger(session, metric.code, metric)
    return format_versions(rows, metric.version)


def format_versions(rows: list[Obj], current: str) -> list[Obj]:
    actual = real_rows(rows)
    # 对同版先展示带真实定义的行；不修改或覆盖旧seed元数据。
    ordered = [*actual, *(row for row in rows if row not in actual)]
    output, seen = [], set()
    for row in ordered:
        version = row.get("ver")
        if not isinstance(version, str) or version in seen:
            continue
        seen.add(version)
        snapshots = [r["definition"] for r in actual if r.get("ver") == version]
        unambiguous = bool(snapshots) and all(snapshot == snapshots[0] for snapshot in snapshots)
        can = unambiguous and VERSION.fullmatch(version) is not None and version != current
        result = {k: deepcopy(v) for k, v in row.items() if k != "definition"}
        result.update(status="生效中" if version == current else "已替代", canRollback=can)
        if not can:
            result["rollbackUnavailable"] = (
                "当前生效版本"
                if version == current
                else ("未保存该版本定义" if not snapshots else "该版本存在不同定义，无法确定回滚目标")
            )
        output.append(result)
    return sorted(
        output,
        key=lambda row: int(VERSION.fullmatch(row["ver"])[1]) if VERSION.fullmatch(row["ver"]) else -1,
        reverse=True,
    )


class MetricRollbackIn(ApiModel):
    to_version: Annotated[str, StringConstraints(pattern=r"^v[1-9][0-9]{0,14}$")]
    cr_id: str | None = Field(None, min_length=1, max_length=64)
    lock_version: int | None = Field(None, ge=0)
    title: str | None = Field(None, min_length=1, max_length=40)
    reason: str = Field("", max_length=4000)
    reviewer: str | None = Field(None, max_length=64)

    @model_validator(mode="after")
    def validated(self):
        ItemIn.jsonb_text(self.model_dump())
        if self.cr_id and self.lock_version is None:
            raise ValueError("加入变更单必须带lockVersion")
        return self


async def rollback(session, user: CurrentUser, code: str, body: MetricRollbackIn) -> Obj:
    check_func(user, "metrics", "manage")
    metric = await session.scalar(
        select(Metric).where(Metric.code == code).execution_options(populate_existing=True)
    )
    if metric is None:
        raise NotFound("指标不存在")
    if metric.version == body.to_version:
        raise conflict("该口径版本已生效，无需回滚", "METRIC_ALREADY_CURRENT")
    history = await ledger(session, code, metric)
    snapshots = [r["definition"] for r in real_rows(history) if r.get("ver") == body.to_version]
    if not snapshots:
        raise conflict("未保存该版本定义，不能根据历史说明推造口径", "METRIC_VERSION_UNAVAILABLE")
    if any(snapshot != snapshots[0] for snapshot in snapshots):
        raise conflict("该版本存在不同定义，无法确定回滚目标", "METRIC_VERSION_AMBIGUOUS")
    target = TargetIn(kind="metric", id=code, name=metric.display_name)
    svc = CrWriteService(session)
    if body.cr_id:
        cr = await svc.lock_draft(body.cr_id, user, body.lock_version)
        cr_id = cr.code
    else:
        cr_id = await svc.create(
            user,
            CRCreateIn(
                title=body.title or f"{metric.display_name}回滚{body.to_version}"[:40],
                reason=body.reason or f"恢复真实已保存的指标口径{body.to_version}",
                reviewer=body.reviewer,
                target=target,
            ),
        )
        cr = await svc.lock_draft(cr_id, user)
    await svc.add_target(cr_id, user, target)
    if await session.scalar(
        select(CrItem.id)
        .where(CrItem.cr_id == cr.id, CrItem.target_kind == "metric", CrItem.target_id == code)
        .limit(1)
    ):
        raise conflict("该指标已有编辑内容，请先处理后再回滚；不会覆盖已有草稿", "METRIC_DRAFT_NOT_EMPTY")
    before, wanted = definition(metric), snapshots[0]
    rows = [
        ItemIn(key=key, op="update", before=before[key], after=deepcopy(wanted[key]))
        for key in FIELDS
        if before[key] != wanted[key]
    ]
    # 旧账本行没有 execDef：不知道当时的执行口径，保持当前值，不当作「无」删除。
    if isinstance(wanted.get("execDef"), dict):
        old, new = before["execDef"], wanted["execDef"]
        rows.extend(
            ItemIn(key=key, op="delete", before=old[key])
            if key not in new
            else ItemIn(key=key, op="update", before=old.get(key), after=new[key])
            for key in EXEC_KEYS
            if old.get(key) != new.get(key) and (key != "defaultRange" or key in new)
        )
    rows.append(
        ItemIn(key="version", op="update", before=metric.version, after=next_version(metric.version, history))
    )
    await svc.put_items(cr_id, user, ItemsPutIn(target=target, section="def", rows=rows))
    cr = await svc.lock_draft(cr_id, user)
    items = await svc.items(cr.id)
    return {
        "crId": cr_id,
        "lockVersion": cr.lock_version,
        "target": {"kind": "metric", "id": code, "name": metric.display_name, "isNew": False},
        "items": [
            item_out(item) for item in items if item.target_kind == "metric" and item.target_id == code
        ],
    }
