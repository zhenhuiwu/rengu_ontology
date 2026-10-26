"""固定受控对象的质量检查；不执行用户 SQL，不生成演示结果。"""

import re
from time import perf_counter

from sqlalchemy import case, func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser
from app.common.pagination import PageParams
from app.common.row_filter import apply_row_filter
from app.core import clock
from app.core.errors import AppError, NotFound
from app.core.schema import _fmt_dt
from app.modules.biz.governance import ORDER_COLUMNS, ORDER_LINKS, PRODUCT_COLUMNS, STORE_COLUMNS
from app.modules.biz.models import BizOrder, OrderLine, Product, Store
from app.modules.data.quality_models import QualityCheck, QualityRun
from app.modules.static.models import ObjectType

# name, ORM, business key, null field, source time, foreign reference
TARGETS = {
    "order": (
        BizOrder,
        BizOrder.code,
        BizOrder.store_id,
        BizOrder.created_at_biz,
        (BizOrder.store_id, Store.id),
    ),
    "order_line": (OrderLine, OrderLine.id, OrderLine.product_id, None, (OrderLine.product_id, Product.id)),
    "store": (Store, Store.code, Store.big_region, None, None),
    "product": (Product, Product.code, Product.category, None, None),
}
KINDS = ["新鲜度", "空值率", "唯一性", "引用完整性"]


def kinds(code: str) -> list[str]:
    t = TARGETS.get(code)
    return [
        k for k in KINDS if t and (k not in {"新鲜度", "引用完整性"} or (t[3] if k == "新鲜度" else t[4]))
    ]


def rule_text(code: str, kind: str) -> str:
    t = TARGETS[code]
    if kind == "新鲜度":
        return "最新下单时间距检测时刻的延迟"
    if kind == "空值率":
        return f"{t[2].key} 为空的比例"
    if kind == "唯一性":
        return f"{t[1].key} 不重复"
    return f"{t[4][0].key} 引用 {t[4][1].table.name}.{t[4][1].key}"


def limit_value(kind: str, threshold: str) -> float:
    patterns = {
        "新鲜度": r"(?:延迟\s*)?(?:≤|<=)\s*(\d+(?:\.\d+)?)\s*小时",
        "空值率": r"(?:空值率\s*)?(?:≤|<=)\s*(\d+(?:\.\d+)?)\s*%",
        "唯一性": r"重复\s*(?:(?:≤|<=)\s*)?(\d+)\s*条",
        "引用完整性": r"找不到\s*(?:(?:≤|<=)\s*)?(\d+)\s*条",
    }
    match = re.fullmatch(patterns[kind], threshold.strip())
    if not match or (kind == "空值率" and float(match[1]) > 100):
        raise AppError(
            "阈值格式无效，请按提示填写",
            code="VALIDATION_FAILED",
            status_code=422,
            details={"fields": [{"field": "threshold", "message": "仅接受小时、百分比或条数阈值"}]},
        )
    return float(match[1])


def checks_query(owner: str, *, codes=None, include_retired=False):
    """供总览复用：我的检查定义，不含演示数据。"""
    query = select(QualityCheck).where(QualityCheck.created_by == owner)
    if not include_retired:
        query = query.where(QualityCheck.retired_at.is_(None))
    if codes is not None:
        query = query.where(QualityCheck.code.in_(codes))
    return query


def latest_runs_query(owner: str, *, codes=None, before=None, include_retired=False):
    """每个可见检查最后一条真实运行；未运行的检查不出现在结果中。"""
    last = (
        select(func.max(QualityRun.id))
        .where(
            QualityRun.check_id.in_(
                checks_query(owner, codes=codes, include_retired=include_retired).with_only_columns(
                    QualityCheck.id
                )
            )
        )
        .group_by(QualityRun.check_id)
    )
    if before is not None:
        last = last.where(QualityRun.time <= before)
    return select(QualityRun).where(QualityRun.id.in_(last))


async def meta(session: AsyncSession, ver: str | None = None) -> list[dict]:
    if ver:
        from app.modules.versioning.repo import ReleaseRepo
        from app.modules.versioning.service import VersioningService

        await ReleaseRepo(session).by_ver_or_404(ver)
        snapshot = await VersioningService().get_snapshot(session, ver) or {}
        return [
            {
                "key": code,
                "name": row.get("name", code),
                "kinds": kinds(code),
                "rules": {k: rule_text(code, k) for k in kinds(code)},
            }
            for code, row in snapshot.get("objectTypes", {}).items()
        ]
    return [
        {
            "key": t.code,
            "name": t.display_name,
            "kinds": kinds(t.code),
            "rules": {k: rule_text(t.code, k) for k in kinds(t.code)},
        }
        for t in (await session.scalars(select(ObjectType).order_by(ObjectType.id))).all()
    ]


def run_out(run: QualityRun) -> dict:
    return {
        "id": str(run.id),
        "jobId": run.job_id,
        "time": _fmt_dt(run.time),
        "result": run.result,
        "actual": run.actual,
        "ms": run.ms,
        "dataCutoff": _fmt_dt(run.data_cutoff),
        "sourceTime": _fmt_dt(run.source_time) if run.source_time else None,
        "definition": run.definition,
    }


async def get_check(session: AsyncSession, owner: str, code: str, *, lock: bool = False):
    query = select(QualityCheck).where(QualityCheck.created_by == owner, QualityCheck.code == code)
    if lock:
        query = query.with_for_update()
    check = await session.scalar(query)
    if check is None:
        raise NotFound("质量检查不存在")
    obj = await session.get(ObjectType, check.object_type_id)
    return check, obj


async def definitions(session, owner, ver=None):
    from app.modules.data.quality_cr import definition

    if ver:
        from app.modules.versioning.repo import ReleaseRepo
        from app.modules.versioning.service import VersioningService

        await ReleaseRepo(session).by_ver_or_404(ver)
        snapshot = await VersioningService().get_snapshot(session, ver) or {}
        return {
            code: value
            for code, value in snapshot.get("qualityChecks", {}).items()
            if value.get("ownerId") == owner
        }
    pairs = (
        await session.execute(
            select(QualityCheck, ObjectType).join(ObjectType).where(QualityCheck.created_by == owner)
        )
    ).all()
    return {
        c.code: {**definition(c, o), "ownerId": c.created_by, "retired": c.retired_at is not None}
        for c, o in pairs
    }


async def list_checks(session, owner, page: PageParams, type_key=None, kind=None, result=None, ver=None):
    from app.modules.data.quality_cr import definition, pending
    from app.modules.versioning.repo import ReleaseRepo

    release = await ReleaseRepo(session).by_ver_or_404(ver) if ver else None
    drafts = {} if ver else await pending(session, owner)
    historical = await definitions(session, owner, ver) if ver else None
    latest = (
        select(QualityRun.check_id, func.max(QualityRun.id).label("run_id"))
        .join(QualityCheck)
        .where(QualityCheck.created_by == owner)
    )
    if release:
        latest = latest.where(QualityRun.time <= release.published_at)
    latest = latest.group_by(QualityRun.check_id).subquery()
    if historical is None:
        base = select(
            QualityCheck.code.label("code"),
            ObjectType.code.label("type_key"),
            QualityCheck.kind.label("kind"),
            func.coalesce(QualityRun.result, "未运行").label("result"),
            QualityCheck.retired_at.is_not(None).label("retired"),
            literal(True).label("published"),
        )
        base = (
            base.select_from(QualityCheck)
            .join(ObjectType, ObjectType.id == QualityCheck.object_type_id)
            .outerjoin(latest, latest.c.check_id == QualityCheck.id)
            .outerjoin(QualityRun, QualityRun.id == latest.c.run_id)
            .where(QualityCheck.created_by == owner)
        )
        selects = [base]
    else:
        selects = []
        for code, value in historical.items():
            run_result = (
                select(QualityRun.result)
                .join(QualityCheck)
                .where(
                    QualityCheck.code == code,
                    QualityCheck.created_by == owner,
                    QualityRun.time <= release.published_at,
                )
                .order_by(QualityRun.id.desc())
                .limit(1)
                .scalar_subquery()
            )
            selects.append(
                select(
                    literal(code).label("code"),
                    literal(value["typeKey"]).label("type_key"),
                    literal(value["kind"]).label("kind"),
                    func.coalesce(run_result, "未运行").label("result"),
                    literal(bool(value.get("retired"))).label("retired"),
                    literal(True).label("published"),
                )
            )
    for code, draft in drafts.items():
        if draft["isNew"]:
            value = draft["definition"]
            selects.append(
                select(
                    literal(code).label("code"),
                    literal(value["typeKey"]).label("type_key"),
                    literal(value["kind"]).label("kind"),
                    literal("未运行").label("result"),
                    literal(False).label("retired"),
                    literal(False).label("published"),
                ).where(~select(QualityCheck.id).where(QualityCheck.code == code).exists())
            )
    if not selects:
        selects = [
            select(
                literal("").label("code"),
                literal("").label("type_key"),
                literal("").label("kind"),
                literal("").label("result"),
                literal(False).label("retired"),
                literal(False).label("published"),
            ).where(literal(False))
        ]
    candidates = (union_all(*selects) if len(selects) > 1 else selects[0]).subquery()
    scope = select(candidates)
    if type_key:
        scope = scope.where(candidates.c.type_key == type_key)
    if kind:
        scope = scope.where(candidates.c.kind == kind)
    if result and result != "全部":
        scope = scope.where(candidates.c.result == result)
    total = await session.scalar(select(func.count()).select_from(scope.subquery()))
    p = page.clamp(total)
    codes = list(
        await session.scalars(
            scope.with_only_columns(candidates.c.code)
            .order_by(case((candidates.c.result == "失败", 0), else_=1), candidates.c.code)
            .offset(p.offset)
            .limit(p.size)
        )
    )
    pairs = (
        await session.execute(
            select(QualityCheck, ObjectType)
            .join(ObjectType)
            .where(QualityCheck.created_by == owner, QualityCheck.code.in_(codes))
        )
    ).all()
    checks = {c.code: c for c, _ in pairs}
    values = (
        historical
        if historical is not None
        else {c.code: {**definition(c, o), "retired": c.retired_at is not None} for c, o in pairs}
    )
    object_codes = {(values.get(code) or drafts[code]["definition"])["typeKey"] for code in codes}
    objects = {
        o.code: o for o in await session.scalars(select(ObjectType).where(ObjectType.code.in_(object_codes)))
    }
    historical_names = {row["key"]: row["name"] for row in await meta(session, ver)} if ver else {}
    ids = [c.id for c in checks.values()]
    page_runs = {
        r.check_id: r
        for r in await session.scalars(
            select(QualityRun)
            .join(latest, QualityRun.id == latest.c.run_id)
            .where(QualityRun.check_id.in_(ids))
        )
    }
    previous_ok = (
        select(QualityRun.check_id, func.max(QualityRun.id).label("id"))
        .join(latest, QualityRun.check_id == latest.c.check_id)
        .where(QualityRun.check_id.in_(ids), QualityRun.id <= latest.c.run_id, QualityRun.result != "失败")
        .group_by(QualityRun.check_id)
        .subquery()
    )
    failed_starts = dict(
        (
            await session.execute(
                select(QualityRun.check_id, func.min(QualityRun.time))
                .join(latest, latest.c.check_id == QualityRun.check_id)
                .outerjoin(previous_ok, previous_ok.c.check_id == QualityRun.check_id)
                .where(
                    QualityRun.check_id.in_(ids),
                    QualityRun.result == "失败",
                    QualityRun.id <= latest.c.run_id,
                    QualityRun.id > func.coalesce(previous_ok.c.id, 0),
                )
                .group_by(QualityRun.check_id)
            )
        ).all()
    )
    rows = []
    for code in codes:
        published = values.get(code)
        value = published or drafts[code]["definition"]
        obj, check = objects.get(value["typeKey"]), checks.get(code)
        run = page_runs.get(check.id) if check else None
        since = failed_starts.get(check.id) if check and run and run.result == "失败" else None
        rows.append(
            {
                "id": code,
                **{k: value[k] for k in ("name", "typeKey", "kind", "threshold", "schedule")},
                "typeName": historical_names.get(value["typeKey"], value["typeKey"])
                if ver
                else obj.display_name
                if obj
                else value["typeKey"],
                "rule": rule_text(value["typeKey"], value["kind"])
                if value["kind"] in kinds(value["typeKey"])
                else "—",
                "actual": run.actual if run else "尚未运行",
                "result": run.result if run else "未运行",
                "lastRun": _fmt_dt(run.time) if run else "",
                "failSince": _fmt_dt(since) if since else None,
                "history": [],
                "retired": bool(published and published.get("retired")),
                "hasPublished": published is not None,
                "note": "",
                "pending": drafts.get(code),
            }
        )
    active = select(candidates).where(candidates.c.published, ~candidates.c.retired).subquery()
    counts = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(active.c.result == "通过"),
                func.count().filter(active.c.result == "失败"),
                func.count().filter(active.c.result == "未运行"),
            ).select_from(active)
        )
    ).one()
    times = (
        select(func.max(QualityRun.data_cutoff), func.max(QualityRun.time))
        .join(QualityCheck)
        .where(QualityCheck.created_by == owner)
    )
    if release:
        times = times.where(QualityRun.time <= release.published_at)
    cutoff, last_time = (await session.execute(times)).one()
    return {
        "items": rows,
        "total": total,
        "page": p.page,
        "size": p.size,
        "dataCutoff": _fmt_dt(cutoff) if cutoff else "",
        "summary": {
            "total": counts[0],
            "passed": counts[1],
            "failed": counts[2],
            "unrun": counts[3],
            "lastRun": _fmt_dt(last_time) if last_time else "",
        },
    }


async def visible_query(user: CurrentUser, code: str):
    model = TARGETS[code][0]
    query = select(model.__table__)
    if code == "order_line":
        orders = await apply_row_filter(select(BizOrder.id), user, "order", ORDER_COLUMNS, ORDER_LINKS)
        return query.where(OrderLine.order_id.in_(orders))
    columns = {"order": ORDER_COLUMNS, "store": STORE_COLUMNS, "product": PRODUCT_COLUMNS}[code]
    return await apply_row_filter(query, user, code, columns, ORDER_LINKS if code == "order" else {})


async def evaluate(session: AsyncSession, user: CurrentUser, code: str, kind: str, limit: float):
    t = TARGETS[code]
    source = (await visible_query(user, code)).subquery()
    total = await session.scalar(select(func.count()).select_from(source))
    if not total:
        return "无数据", "当前可见范围没有记录，未判定通过或失败", None
    latest = None
    if kind == "新鲜度":
        latest = await session.scalar(select(func.max(source.c[t[3].key])))
        if latest is None:
            return "无数据", "没有有效的业务时间", None
        value = (clock.now() - latest).total_seconds() / 3600
        actual = f"最新下单时间 {_fmt_dt(latest)}，延迟 {value:.2f} 小时"
        passed = 0 <= value <= limit
    elif kind == "空值率":
        count = await session.scalar(
            select(func.count()).select_from(source).where(source.c[t[2].key].is_(None))
        )
        value = count / total * 100
        actual, passed = f"{value:.2f}%（{count}/{total} 条）", value <= limit
    elif kind == "唯一性":
        distinct = await session.scalar(select(func.count(func.distinct(source.c[t[1].key]))))
        value = total - distinct
        actual, passed = f"重复 {value} 条（按 {t[1].key}）", value <= limit
    else:
        local, foreign = t[4]
        value = await session.scalar(
            select(func.count())
            .select_from(source)
            .where(~select(foreign).where(foreign == source.c[local.key]).exists())
        )
        actual, passed = f"找不到 {value} 条", value <= limit
    return "通过" if passed else "失败", actual, latest


async def execute_check(session: AsyncSession, user: CurrentUser, code: str, job_id: str) -> QualityRun:
    check, obj = await get_check(session, user.id, code, lock=True)
    if check.retired_at:
        raise AppError("已退休检查不可运行", code="STATE_INVALID", status_code=409)
    started, now = perf_counter(), clock.now()
    result, actual, source_time = await evaluate(session, user, obj.code, check.kind, check.limit_value)
    run = QualityRun(
        check_id=check.id,
        job_id=job_id,
        time=now,
        result=result,
        actual=actual,
        ms=max(0, round((perf_counter() - started) * 1000)),
        data_cutoff=now,
        source_time=source_time,
        definition={
            "name": check.name,
            "typeKey": obj.code,
            "kind": check.kind,
            "rule": rule_text(obj.code, check.kind),
            "threshold": check.threshold,
            "schedule": check.schedule,
        },
        created_by=user.id,
    )
    session.add(run)
    await session.flush()
    return run
