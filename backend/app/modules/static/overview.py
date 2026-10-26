"""总览只读聚合（api-index §3.4）；没有质量检查表时明确标记未配置。"""

from sqlalchemy import func, select

from app.common.auth import CurrentUser
from app.common.pagination import PageParams
from app.core import clock
from app.core.schema import _fmt_dt
from app.modules.action.service import ActionTypeService
from app.modules.data.quality import checks_query, definitions, latest_runs_query
from app.modules.dynamic.models import ActionRun, ActionStatus
from app.modules.event.models import Event, EventOutcome, EventType
from app.modules.event.service import EventTypeService
from app.modules.static import shapes
from app.modules.static.models import ChangeRequest, CRStatus, CrTarget
from app.modules.static.service import StaticService, paginate
from app.modules.versioning.repo import ReleaseRepo


async def quality_state(svc: StaticService, user: CurrentUser):
    owner = svc.ctx.preview_as or user.id
    options = {}
    if svc.ver:
        published = await definitions(svc.session, owner, svc.ver)
        options = {
            "codes": [code for code, value in published.items() if not value.get("retired")],
            "include_retired": True,
        }
    checks = (await svc.session.scalars(checks_query(owner, **options))).all()
    release = await ReleaseRepo(svc.session).by_ver_or_404(svc.ver) if svc.ver else None
    runs = (
        await svc.session.scalars(
            latest_runs_query(owner, **options, before=release.published_at if release else None)
        )
    ).all()
    return checks, {r.check_id: r for r in runs}


async def overview(svc: StaticService, user: CurrentUser) -> dict:
    await svc._check_ver()
    objects = [shapes.object_list_item(o, 0) for o in await svc.repo.object_types()]
    metrics = [shapes.metric_list_item(m) for m in await svc.repo.metrics()]
    if svc.ver:
        objects = list((await svc._section("objectTypes") or {}).values())
        metrics = list((await svc._section("metrics") or {}).values())
    links = await svc._links()
    dims = [d for d in await svc._dims() if d.get("status") != "已驳回"]
    actions = await ActionTypeService(svc.session).defs(svc.ctx)
    events = await EventTypeService(svc.session).defs(svc.ctx)
    quality_checks, quality_runs = await quality_state(svc, user)
    quality_health = []
    if quality_checks:
        failed = sum(r.result == "失败" for r in quality_runs.values())
        pending = (
            len(quality_checks) - len(quality_runs) + sum(r.result == "无数据" for r in quality_runs.values())
        )
        quality_health = [
            {"n": failed, "text": "项质量检查失败", "tone": "failed"},
            {"n": pending, "text": "项质量检查未运行 / 无数据", "tone": "attention"},
        ]
    changes: dict[str, set[str]] = {}
    if not svc.ver:
        rows = await svc.session.execute(
            select(CrTarget.target_kind, CrTarget.target_id)
            .join(ChangeRequest, ChangeRequest.id == CrTarget.cr_id)
            .where(ChangeRequest.status.in_([CRStatus.draft, CRStatus.in_review, CRStatus.approved]))
        )
        for kind, code in rows:
            changes.setdefault(kind, set()).add(code)
    run_counts = dict(
        (
            await svc.session.execute(
                select(ActionRun.status, func.count())
                .where(
                    ActionRun.action_type_code.in_([a["id"] for a in actions]), ActionRun.in_center.is_(False)
                )
                .group_by(ActionRun.status)
            )
        ).all()
    )
    failed_events = await svc.session.scalar(
        select(func.count())
        .select_from(Event)
        .join(EventType, EventType.id == Event.event_type_id)
        .where(
            EventType.code.in_([e["id"] for e in events]),
            Event.is_test.is_(False),
            Event.outcome == EventOutcome.failed,
        )
    )

    def health(n: int, text: str, tone: str = "normal") -> dict:
        return {"n": n, "text": text, "tone": tone}

    def card(key: str, title: str, count: int, unit: str, items: list[dict]) -> dict:
        return {"key": key, "title": title, "count": count, "unit": unit, "health": items}

    published = lambda rows: sum(r.get("status", "已发布") == "已发布" for r in rows)  # noqa: E731
    changing = lambda kind, rows: len(  # noqa: E731
        {r["id"] for r in rows if r.get("status", "已发布") not in {"已发布", "已下线", "已停用"}}
        | changes.get(kind, set())
    )
    return {
        "cards": [
            card(
                "obj",
                "对象类型",
                published(objects),
                "个",
                [health(changing("object", objects), "个变更中 / 草稿"), *quality_health],
            ),
            card(
                "link",
                "关系类型",
                published(links),
                "条",
                [
                    health(changing("link", links), "条变更中 / 草稿"),
                    health(
                        sum((link.get("quality") or {}).get("orphans", 0) > 0 for link in links),
                        "条有孤儿记录",
                    ),
                ],
            ),
            card(
                "action",
                "动作类型",
                len(actions),
                "个",
                [
                    health(changing("action", actions), "个变更中 / 草稿"),
                    health(run_counts.get(ActionStatus.unknown, 0), "次结果未知", "attention"),
                    health(run_counts.get(ActionStatus.failed, 0), "次执行失败", "failed"),
                ],
            ),
            card(
                "event",
                "事件类型",
                len(events),
                "个",
                [
                    health(changing("event", events), "个变更中 / 草稿"),
                    health(failed_events or 0, "次投递失败", "failed"),
                ],
            ),
            card(
                "metric",
                "指标",
                published(metrics),
                "个",
                [
                    health(changing("metric", metrics), "个变更中 / 草稿"),
                    health(sum(m.get("status") == "已下线" for m in metrics), "个已下线"),
                ],
            ),
            card(
                "dim",
                "维度与字典",
                len(dims),
                "个",
                [
                    health(sum(d.get("kind") == "维度" for d in dims), "个维度"),
                    health(sum(d.get("kind") == "码表" for d in dims), "个码表"),
                    health(changing("dimension", dims), "个变更中 / 草稿"),
                ],
            ),
        ],
        "qualityConfigured": bool(quality_checks),
        "dataAsOf": _fmt_dt(clock.now()),
    }


async def todos(svc: StaticService, kind: str, page: PageParams, user: CurrentUser) -> dict:
    await svc._check_ver()
    items = []
    quality_checks, quality_runs = await quality_state(svc, user)
    if not svc.ver and kind != "变更":
        for check in quality_checks:
            run = quality_runs.get(check.id)
            if run is not None and run.result == "失败":
                items.append(
                    {
                        "id": check.code,
                        "kind": "数据质量",
                        "title": check.name,
                        "who": f"实际 {run.actual}",
                        "status": "失败",
                        "tone": "failed",
                        "time": _fmt_dt(run.time),
                    }
                )
    if not svc.ver and kind != "数据质量":
        rows = await svc.session.scalars(
            select(ChangeRequest)
            .where(
                ChangeRequest.status.in_(
                    [
                        CRStatus.draft,
                        CRStatus.in_review,
                        CRStatus.approved,
                    ]
                )
            )
            .order_by(ChangeRequest.status_at.desc(), ChangeRequest.id.desc())
        )
        for cr in rows:
            reviewing = cr.status == CRStatus.in_review
            items.append(
                {
                    "id": cr.code,
                    "kind": "变更",
                    "title": cr.title,
                    "who": f"审核人 {cr.reviewer}" if reviewing else cr.owner,
                    "status": "已通过 · 待发布" if cr.status == CRStatus.approved else cr.status.value,
                    "tone": "attention" if reviewing else "normal",
                    "time": _fmt_dt(cr.status_at),
                }
            )
    items.sort(key=lambda item: (item["time"], item["id"]), reverse=True)
    return {**paginate(items, page), "qualityConfigured": bool(quality_checks)}
