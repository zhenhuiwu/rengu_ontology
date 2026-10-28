"""Replay saved, version-bound SELECTs with current permissions; never generate or repair SQL."""

import asyncio
import hashlib
import json
import re

from sqlalchemy import select, text
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


async def fingerprint(session, user):
    value = {
        "user": user.id,
        "ontology": user.ontology_id,
        "filters": await row_filters_for(user, session),
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


def require_knowledge_filters(question, sql, release):
    """Check necessary literals for explicitly value-defining published terms, without inferring a field."""
    from app.modules.qa.value_index import norm

    text = norm(question or "")
    values = set()
    for term in ((release.snapshot or {}).get("terms") or {}).values():
        if not isinstance(term, dict) or term.get("status") != "已发布":
            continue
        name, definition = term.get("name"), term.get("definition")
        if not isinstance(name, str) or not name.strip() or not isinstance(definition, str):
            continue
        aliases = [norm(word) for word in term.get("synonyms") or [] if isinstance(word, str)]
        if not any(len(word) >= 2 and word in text for word in aliases):
            continue
        pattern = r"(?<![不非无])(?:为|等于|=)\s*[\"'“‘]?" + re.escape(name.strip()) + r"[\"'”’]?(?=$|[\s，。；、,.;])"
        if re.search(pattern, definition):
            values.add(name.strip())
    if not values:
        return
    tree = assert_single_select(sql)
    if not isinstance(tree, exp.Select):
        raise unavailable("知识同义词条件无法验证，请重新提问并固定回答")
    where = tree.args.get("where")
    if where is None or where.find(exp.Or) or where.find(exp.Not):
        raise unavailable("知识同义词筛选条件缺失或有歧义，请重新提问并固定回答")
    pending, terms = [where.this], []
    while pending:
        node = pending.pop()
        if isinstance(node, exp.Paren):
            pending.append(node.this)
        elif isinstance(node, exp.And):
            pending.extend((node.this, node.expression))
        else:
            terms.append(node)
    found = set()
    for node in terms:
        if isinstance(node, exp.EQ):
            for column, value in ((node.this, node.expression), (node.expression, node.this)):
                if isinstance(column, exp.Column) and isinstance(value, exp.Literal) and value.is_string:
                    found.add(value.this)
        elif isinstance(node, exp.In) and isinstance(node.this, exp.Column) and len(node.expressions) == 1:
            value = node.expressions[0]
            if isinstance(value, exp.Literal) and value.is_string:
                found.add(value.this)
    if not values <= found:
        raise unavailable("原查询缺少知识同义词对应的规范值筛选，请重新提问并固定回答")


async def turn_plan(session, turn, release):
    """Legacy real successful SQL is reusable only with an explicit matching release."""
    if (
        turn.is_seed
        or getattr(turn.kind, "value", turn.kind) not in ("answer", "empty")
        or getattr(turn.status, "value", turn.status) != "done"
        or not turn.sql_text
        or release is None
        or not turn.release_id
        or not turn.answered_ver
    ):
        raise unavailable("该回答缺少真实成功查询或明确发布依据，不能固定旧结果")
    plan = (turn.sem_plan or {}).get("execution")
    original = await ReleaseRepo(session).by_ver(turn.answered_ver)
    if original is None or original.id != turn.release_id:
        raise unavailable("回答版本依据不一致，请重新提问")
    if plan is not None:
        if not isinstance(plan, dict) or plan.get("kind") != PLAN_KIND or plan.get("sql") != turn.sql_text or plan.get("releaseId") != original.id or plan.get("releaseVer") != original.ver:
            raise unavailable("回答执行定义不一致，不能固定旧结果")
    if not any(assert_single_select(turn.sql_text).find_all(exp.Table)):
        raise unavailable("回答没有明确的本体数据源，不能固定")
    from types import SimpleNamespace
    from app.common.sql_guard import ensure_governed_tables
    await ensure_governed_tables(session)
    card = SimpleNamespace(viz="表格", pending_cr=None, source_turn_id=turn.id,
                           metric_code=getattr(turn, "metric_code", None),
                           sem_plan={"execution": plan or saved_plan(turn.sql_text, original)})
    return await validate_plan(card, release, session)


async def validate_plan(card, release, session):
    if getattr(card.viz, "value", card.viz) != "表格":
        raise unavailable("卡片未保存明确的图形映射，不能推断展示结果")
    plan = execution(card)
    if release is None or not isinstance(plan.get("releaseVer"), str):
        raise unavailable("卡片缺少明确的原始版本")
    original = await ReleaseRepo(session).by_ver(plan["releaseVer"])
    if original is None or plan.get("releaseId") != original.id:
        raise unavailable("卡片原始版本依据不一致")
    if card.pending_cr:
        raise unavailable("卡片口径待更新，请先确认已发布口径")
    public_columns(plan["sql"], original, session)
    public_columns(plan["sql"], release, session)
    from app.modules.qa.models import QaTurn
    turn_id = getattr(card, "source_turn_id", None)
    turn = await session.scalar(select(QaTurn).where(QaTurn.id == turn_id)) if turn_id else None
    if turn_id and (turn is None or turn.is_seed or getattr(turn.kind, "value", turn.kind) not in ("answer", "empty")
                    or getattr(turn.status, "value", turn.status) != "done" or turn.sql_text != plan["sql"]):
        raise unavailable("卡片来源不是一致的真实成功查询，请重新固定回答")
    if turn is not None:
        require_knowledge_filters(turn.question, plan["sql"], release)
    if original.id != release.id:
        from app.common.sql_guard import _real_tables
        if (turn is None or turn.is_seed or getattr(turn.kind, "value", turn.kind) not in ("answer", "empty")
            or getattr(turn.status, "value", turn.status) != "done" or turn.sql_text != plan["sql"]):
            raise unavailable("缺少真实成功查询的来源，不能跨版本重算")
        turn_release = await ReleaseRepo(session).by_ver(turn.answered_ver) if turn.answered_ver else None
        if turn_release is None or turn_release.id != turn.release_id:
            raise unavailable("原始回答的版本依据不一致")
        turn_plan_data = (turn.sem_plan or {}).get("execution")
        if turn_plan_data is not None and (not isinstance(turn_plan_data, dict) or turn_plan_data.get("kind") != PLAN_KIND
            or turn_plan_data.get("sql") != turn.sql_text or turn_plan_data.get("releaseId") != turn.release_id
            or turn_plan_data.get("releaseVer") != turn.answered_ver):
            raise unavailable("原始回答执行依据不一致")
        tree = assert_single_select(plan["sql"])
        bindings = governed_tables(require_ontology_id(session), session)
        names = {table.name for table in _real_tables(tree)}
        if not names or any(name not in bindings for name in names):
            raise unavailable("查询引用的受控来源已变化")
        referenced = {name: set() for name in names}
        for scope in traverse_scope(tree):
            sources = {alias: source.name for alias, (_, source) in scope.selected_sources.items() if isinstance(source, exp.Table)}
            for column in scope.columns:
                if column.table in sources:
                    referenced[sources[column.table]].add(column.name)
                elif not column.table:
                    candidates = [name for name in sources.values() if column.name in bindings[name].table.c]
                    if len(candidates) == 1:
                        referenced[candidates[0]].add(column.name)
        old, new = original.snapshot or {}, release.snapshot or {}
        for table, columns in referenced.items():
            binding = bindings[table]
            code = binding.object_type
            old_obj = (old.get("objectTypes") or {}).get(code) or {}
            new_obj = (new.get("objectTypes") or {}).get(code) or {}
            if any(old_obj.get(k) != new_obj.get(k) for k in ("api", "source", "table", "pk", "status")):
                raise unavailable("查询引用的对象来源或主键已变化")
            if not binding.inherit and old_obj.get("table") not in (table, f"public.{table}"):
                raise unavailable("历史对象来源与当前受控主表不一致")
            if binding.inherit and binding.object_type == binding.inherit.parent.object_type:
                if not (old.get("objectBindings") or {}).get(code) or not (new.get("objectBindings") or {}).get(code):
                    raise unavailable("历史版本缺少补充表绑定依据")
            if old.get("objectBindings") is not None or new.get("objectBindings") is not None:
                old_binding = (old.get("objectBindings") or {}).get(code)
                new_binding = (new.get("objectBindings") or {}).get(code)
                if old_binding is not None and new_binding is None:
                    raise unavailable("当前版本缺少原受控绑定依据")
                if new_binding is not None:
                    parent = binding.inherit.parent if binding.inherit and binding.object_type == binding.inherit.parent.object_type else binding
                    if new_binding.get("table") != parent.name:
                        raise unavailable("当前主表与发布绑定不一致")
                    if binding is parent:
                        if not columns <= set(new_binding.get("readColumns") or []):
                            raise unavailable("当前版本未授权查询引用的字段")
                        if old_binding is not None and (old_binding.get("table") != parent.name or not columns <= set(old_binding.get("readColumns") or [])):
                            raise unavailable("原版本未授权查询引用的字段")
                    else:
                        current_spec = (session.info.get("supplementary_bindings", {}).get(code) or ({}, None))[0]
                        new_spec = new_binding.get("supplementary") or {}
                        old_spec = (old_binding or {}).get("supplementary") or {}
                        keys = ("table", "mainKey", "joinKey")
                        if any(old_spec.get(k) != new_spec.get(k) or new_spec.get(k) != current_spec.get(k) for k in keys):
                            raise unavailable("补充表关联绑定已变化")
                        for column in columns:
                            if column == new_spec.get("joinKey"):
                                continue
                            def mapped(spec):
                                return {api for api, value in (spec.get("fields") or {}).items() if value == column}
                            if not mapped(old_spec) or mapped(old_spec) != mapped(new_spec) or mapped(new_spec) != mapped(current_spec):
                                raise unavailable("补充表字段映射已变化")
            for column in columns:
                if binding.inherit and binding.object_type == binding.inherit.parent.object_type and column == binding.inherit.local.key:
                    continue
                def definition(snapshot):
                    props = (snapshot.get("properties") or {}).get(code) or {}
                    matches = []
                    for n, (api, prop) in enumerate(props.items()):
                        flags = snapshot_flags(code, {**prop, "api": api}, n, release.ontology_id)
                        if (flags.get("backing_column") or prop.get("source")) == column:
                            matches.append((api, prop, flags))
                    if len(matches) != 1:
                        return None
                    api, prop, flags = matches[0]
                    return (api, prop.get("source"), prop.get("type"), bool(prop.get("sensitive")), flags["visibility"], prop.get("enumName"))
                if definition(old) is None or definition(old) != definition(new):
                    raise unavailable("查询引用的属性映射、类型或权限已变化")
        objects = {bindings[name].object_type for name in names}
        if len(names) > 1:
            def related(snapshot):
                return {key: value for key, value in (snapshot.get("links") or {}).items() if value.get("from") in objects and value.get("to") in objects}
            if related(old) != related(new):
                raise unavailable("查询涉及的关系定义已变化")
        metric_code = getattr(card, "metric_code", None)
        if metric_code:
            if (old.get("metrics") or {}).get(metric_code) != (new.get("metrics") or {}).get(metric_code):
                raise unavailable("卡片引用的指标口径已变化")
        elif any(tree.find_all(exp.AggFunc)) and old.get("metrics") != new.get("metrics"):
            raise unavailable("聚合卡片未记录指标来源，口径变化后不能安全重算")
    return saved_plan(plan["sql"], release, plan.get("scope"))


def public_columns(sql, release, session):
    """Reject unprovable fields; replay never expands stars or invents mappings."""
    tree = assert_single_select(sql)
    for star in tree.find_all(exp.Star):
        if not isinstance(star.parent, exp.Count) or star.parent.this is not star:
            raise unavailable("原查询包含未明确的字段投影，请重新固定使用公开字段的回答")
    snapshot = release.snapshot or {}
    allowed = {}
    for name, binding in governed_tables(release.ontology_id, session).items():
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
                and (binding.projection is None or column in binding.projection)
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
    from app.common.sql_guard import ensure_governed_tables

    await ensure_governed_tables(session)
    plans = [await validate_plan(card, release, session) for card in cards]
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
                    result = await run_guarded(
                        connection, wrap_limit(tree, ROW_LIMIT + 1), user, session=session
                    )
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
    from app.common.sql_guard import ensure_governed_tables
    await ensure_governed_tables(session)
    rendered, pending, indexes = [], [], []
    try:
        current = await fingerprint(session, viewer)
    except AppError:
        current = None
    for card in page.cards:
        frame = denied_card(card_out(card))
        try:
            if ctx.ver:
                raise unavailable("历史模式不能用当前数据重算页面")
            plan = await validate_plan(card, release, session)
            if current is None:
                raise unavailable("当前身份没有可执行的数据范围")
            if card.perm_fingerprint == current and execution(card).get("releaseId") == release.id:
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
    for index, card in zip(indexes, pending, strict=True):
        try:
            caches, _ = await execute_cards(session, viewer, [card], release=release)
            rendered[index] = {**denied_card(card_out(card)), **caches[0], "denied": False}
        except AppError as error:
            rendered[index]["unavailable"] = error.message
    return rendered


async def template_capability(session, template):
    release = await ReleaseRepo(session).latest()
    try:
        if not template.cards or len(template.cards) > MAX_CARDS:
            raise unavailable("尚无可执行卡片")
        from app.common.sql_guard import ensure_governed_tables

        await ensure_governed_tables(session)
        errors = []
        for card in template.cards:
            try:
                await validate_plan(card, release, session)
            except AppError as error:
                errors.append(error.message)
        return (True, None) if len(errors) < len(template.cards) else (False, errors[0])
    except AppError as error:
        return False, error.message


async def refresh_compatible_cards(session, user, cards):
    """Re-execute each compatible card and update plans only after the head remains stable."""
    if not cards or len(cards) > MAX_CARDS:
        raise unavailable(f"页面须包含1至{MAX_CARDS}张可执行卡片")
    from app.db.dialects import get_adapter
    from app.modules.versioning.service import PUBLISH_LOCK
    from app.common.sql_guard import ensure_governed_tables
    await get_adapter(session).xact_lock(session, PUBLISH_LOCK)
    await ensure_governed_tables(session)
    release = await ReleaseRepo(session).latest()
    if release is None:
        raise unavailable("当前本体没有可执行版本")
    permission = await fingerprint(session, user)
    successes, failures = [], []
    for card in cards:
        try:
            plan = await validate_plan(card, release, session)
            caches, as_of = await execute_cards(session, user, [card], release=release)
            successes.append((card, caches[0], plan, as_of))
        except AppError as error:
            failures.append((card, error.message))
    latest = await ReleaseRepo(session).latest()
    if latest is None or latest.id != release.id:
        raise unavailable("查询期间发布版本已变化，原结果保持不变")
    for card, cache, plan, _ in successes:
        card.result_cache, card.perm_fingerprint = cache, permission
        card.sem_plan = {**(card.sem_plan or {}), "execution": plan}
    return successes, failures
