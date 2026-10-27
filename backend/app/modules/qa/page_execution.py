"""Replay saved, version-bound SELECTs with current permissions; never generate or repair SQL."""

import asyncio
import hashlib
import json

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, traverse_scope

from app.common.auth import check_func, resolve_user
from app.common.ontology import require_ontology_id
from app.common.sql_guard import assert_single_select, governed_tables, row_filters_for, run_guarded
from app.core.config import get_settings
from app.core.errors import AppError, Forbidden
from app.db.session import QaReadonlyNotConfigured, get_qa_readonly_engine
from app.modules.qa.chain import ROW_LIMIT, _cell, wrap_limit
from app.modules.static.explorer import snapshot_flags
from app.modules.versioning.repo import ReleaseRepo

MAX_CARDS = 20
PLAN_KIND = "guarded-sql-v1"


def unavailable(message):
    return AppError(message, code="PAGE_EXECUTION_UNAVAILABLE", status_code=422)


async def fingerprint(user):
    value = {
        "user": user.id,
        "ontology": user.ontology_id,
        "filters": await row_filters_for(user),
        "func": user.func,
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def saved_plan(sql, release, scope=None):
    if not sql or release is None:
        return None
    assert_single_select(sql)
    return {"kind": PLAN_KIND, "sql": sql, "releaseId": release.id, "releaseVer": release.ver, "scope": scope}


def execution(card):
    plan = (card.sem_plan or {}).get("execution")
    if not isinstance(plan, dict) or plan.get("kind") != PLAN_KIND or not isinstance(plan.get("sql"), str):
        raise unavailable("卡片未保存可执行定义，请重新固定真实回答")
    return plan


def turn_plan(turn, release):
    """Legacy real successful SQL is reusable only with an explicit matching release."""
    if (
        turn.is_seed
        or getattr(turn.kind, "value", turn.kind) != "answer"
        or not turn.sql_text
        or release is None
        or turn.release_id != release.id
        or turn.answered_ver != release.ver
    ):
        raise unavailable("该回答缺少真实成功查询或明确发布依据，不能固定旧结果")
    plan = (turn.sem_plan or {}).get("execution")
    if plan is not None:
        if not isinstance(plan, dict) or plan.get("sql") != turn.sql_text:
            raise unavailable("回答执行定义不一致，不能固定旧结果")
        return plan
    return saved_plan(turn.sql_text, release)


def validate_plan(card, release):
    if getattr(card.viz, "value", card.viz) != "表格":
        raise unavailable("卡片未保存明确的图形映射，不能推断展示结果")
    plan = execution(card)
    if release is None or plan.get("releaseId") != release.id or plan.get("releaseVer") != release.ver:
        raise unavailable("卡片引用的发布版本已变化，请重新确认问题后固定")
    if card.pending_cr:
        raise unavailable("卡片口径待更新，请先确认已发布口径")
    public_columns(plan["sql"], release)
    return plan


def public_columns(sql, release):
    """Reject unprovable fields; replay never expands stars or invents mappings."""
    tree = assert_single_select(sql)
    for star in tree.find_all(exp.Star):
        if not isinstance(star.parent, exp.Count) or star.parent.this is not star:
            raise unavailable("原查询包含未明确的字段投影，请重新固定使用公开字段的回答")
    snapshot = release.snapshot or {}
    allowed = {}
    for name, binding in governed_tables(release.ontology_id).items():
        obj = (snapshot.get("objectTypes") or {}).get(binding.object_type) or {}
        if obj.get("status") != "已发布":
            continue
        allowed[name] = set()
        for n, (api, raw) in enumerate(
            ((snapshot.get("properties") or {}).get(binding.object_type) or {}).items()
        ):
            prop = {**raw, "api": api}
            flags = snapshot_flags(binding.object_type, prop, n, release.ontology_id)
            column = flags.get("backing_column") or prop.get("source")
            if (
                isinstance(column, str)
                and not prop.get("sensitive")
                and flags["visibility"] != "hidden"
                and column in binding.table.c
            ):
                allowed[name].add(column)
    for scope in traverse_scope(tree):
        sources = {}
        for alias, (_, source) in scope.selected_sources.items():
            if isinstance(source, exp.Table):
                if source.name not in allowed:
                    raise unavailable("原查询引用的对象尚未发布或缺少公开字段映射")
                sources[alias] = allowed[source.name]
            elif isinstance(source, Scope):
                sources[alias] = set(source.expression.named_selects) - {"*"}
            else:
                raise unavailable("无法验证原查询的公开字段")
        for column in scope.columns:
            if column.table:
                valid = column.name in sources.get(column.table, set())
            else:
                matches = sum(column.name in names for names in sources.values())
                # GROUP/ORDER aliases only name already-checked SELECT expressions.
                output_alias = any(item.alias == column.name for item in scope.expression.selects)
                valid = matches == 1 or (
                    matches == 0 and output_alias and isinstance(column.parent, (exp.Ordered, exp.Group))
                )
            if not valid:
                raise unavailable("原查询引用未公开、敏感或无法确认的字段，请重新确认回答")


def result_cache(result):
    names = list(result.keys())
    values = [[_cell(value) for value in row] for row in result.fetchall()]
    truncated = len(values) > ROW_LIMIT
    values = values[:ROW_LIMIT]
    columns = [{"key": f"c{i}", "title": str(name)} for i, name in enumerate(names)]
    items = [{f"c{i}": value for i, value in enumerate(row)} for row in values]
    preview = [
        [
            str(row[0]) if row and row[0] is not None else "—",
            " · ".join(f"{names[i]}：{row[i]}" for i in range(1, len(row))) if len(row) > 1 else "",
        ]
        for row in values[:5]
    ]
    return {"columns": columns, "items": items, "rows": preview, "total": len(values), "truncated": truncated}


async def execute_cards(session, user, cards, *, release=None):
    if not cards or len(cards) > MAX_CARDS:
        raise unavailable(f"页面须包含1至{MAX_CARDS}张可执行卡片")
    if user.ontology_id != require_ontology_id(session):
        raise unavailable("页面身份与当前本体不一致")
    check_func(user, "qa-pages", "view")
    release = release or await ReleaseRepo(session).latest()
    plans = [validate_plan(card, release) for card in cards]
    try:
        engine = get_qa_readonly_engine()
        # One existing query timeout bounds the whole page, including connection acquisition.
        async with asyncio.timeout(max(0.001, get_settings().qa_statement_timeout_ms / 1000)):
            async with engine.connect() as connection, connection.begin():
                await connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
                as_of = await connection.scalar(text("SELECT transaction_timestamp()"))
                await connection.execute(
                    text("SELECT set_config('rengu.ontology_id', :id, true)"), {"id": str(user.ontology_id)}
                )
                caches = []
                for plan in plans:
                    tree = assert_single_select(plan["sql"])
                    result = await run_guarded(connection, wrap_limit(tree, ROW_LIMIT + 1), user)
                    caches.append(result_cache(result))
                latest = await ReleaseRepo(session).latest()
                if latest is None or latest.id != release.id:
                    raise unavailable("查询期间发布版本已变化，原结果保持不变")
                return caches, as_of
    except QaReadonlyNotConfigured:
        raise unavailable("只读数据连接未配置，原结果保持不变") from None
    except TimeoutError:
        raise unavailable("页面查询超时，原结果保持不变") from None
    except DBAPIError:
        raise unavailable("原查询当前无法执行，原结果保持不变") from None


async def current_viewer(session, user):
    if user.ontology_ids is not None or user.is_service_account:
        raise Forbidden("页面数据仅供已授权的当前用户读取")
    viewer = await resolve_user(session, user.id, user.tenant_id)
    check_func(viewer, "qa-pages", "view")
    return viewer


async def projected_cards(session, user, page, ctx, *, temporary=False):
    from app.modules.qa.service import card_out, denied_card

    viewer = await current_viewer(session, user)
    release = await ReleaseRepo(session).latest()
    rendered, pending, indexes = [], [], []
    try:
        current = await fingerprint(viewer)
    except AppError:
        current = None
    for card in page.cards:
        frame = denied_card(card_out(card))
        try:
            if ctx.ver:
                raise unavailable("历史模式不能用当前数据重算页面")
            validate_plan(card, release)
            if current is None:
                raise unavailable("当前身份没有可执行的数据范围")
            if card.perm_fingerprint == current:
                rendered.append(card_out(card))
                continue
            if temporary and len(page.cards) <= MAX_CARDS:
                indexes.append(len(rendered))
                pending.append(card)
            else:
                frame["unavailable"] = "需要按当前数据权限重新读取"
        except AppError as error:
            frame["unavailable"] = error.message
        rendered.append(frame)
    if pending:
        try:
            caches, _ = await execute_cards(session, viewer, pending, release=release)
            for index, card, cache in zip(indexes, pending, caches, strict=True):
                rendered[index] = {**denied_card(card_out(card)), **cache, "denied": False}
        except AppError as error:
            for index in indexes:
                rendered[index]["unavailable"] = error.message
    return rendered


async def template_capability(session, template):
    release = await ReleaseRepo(session).latest()
    try:
        if not template.cards or len(template.cards) > MAX_CARDS:
            raise unavailable("尚无可执行卡片")
        for card in template.cards:
            validate_plan(card, release)
        return True, None
    except AppError as error:
        return False, error.message
