"""A published, executable metric may supply the missing time window; never infer one."""

import re
from dataclasses import dataclass

from sqlalchemy import DateTime, select
from sqlalchemy.dialects import postgresql
from sqlglot import exp, parse_one
from sqlglot.optimizer.scope import traverse_scope

from app.common.auth import check_func
from app.common.ontology import require_ontology_id
from app.common.row_filter import is_deny_all
from app.core.errors import AppError
from app.modules.qa import guards, sqlfix
from app.modules.static.metric_defaults import default_range
from app.modules.static.metric_trial import compile_metric, execution_bindings, metric_window
from app.modules.static.metric_versions import exec_def, meta_def
from app.modules.static.models import Metric, MetricStatus, ObjectType


@dataclass(frozen=True)
class PublishedRange:
    code: str
    name: str
    version: str
    table: str
    column: str
    days: int
    start: str
    end: str
    lower: str
    upper: str
    predicates: tuple[str, ...]
    timezone: str
    direct_source: bool = False

    @property
    def note(self):
        return f"按已发布指标{self.name} {self.version}默认最近{self.days}天：{self.start} 至 {self.end}（{self.timezone}）"

    def apply(self, tree):
        # Remove model-invented windows, then enforce this window and the saved metric predicates.
        sqlfix.strip_time_window(tree, frozenset({self.column}))
        count = 0
        for scope in traverse_scope(tree):
            for alias, (_, source) in scope.selected_sources.items():
                if not isinstance(source, exp.Table) or source.name != self.table:
                    continue
                where = scope.expression.args.get("where")
                if where and any(
                    col.name == self.column and col.table in ("", alias, self.table)
                    for col in where.find_all(exp.Column)
                ):
                    raise AppError(
                        "查询未能可靠应用已发布默认时间范围，请重试",
                        code="METRIC_DEFAULT_UNAVAILABLE",
                        status_code=422,
                    )
                column = exp.column(self.column, table=alias)
                conditions = [
                    exp.GTE(this=column.copy(), expression=exp.Literal.string(self.lower)),
                    exp.LT(this=column.copy(), expression=exp.Literal.string(self.upper)),
                ]
                for sql in self.predicates:
                    predicate = parse_one(sql, read="postgres")
                    for col in predicate.find_all(exp.Column):
                        if col.table == self.table:
                            col.set("table", exp.to_identifier(alias))
                    conditions.append(predicate)
                for condition in conditions:
                    scope.expression.where(condition, copy=False)
                count += 1
        if not count:
            raise AppError(
                "查询没有使用该指标获准的对象，不能应用默认时间范围",
                code="METRIC_DEFAULT_UNAVAILABLE",
                status_code=422,
            )


def _matches(question, word):
    if not isinstance(word, str) or len(word.strip()) < 2:
        return False
    word = word.strip()
    if re.fullmatch(r"[\w.-]+", word, flags=re.ASCII):
        return bool(re.search(r"(?<![\w])" + re.escape(word) + r"(?![\w])", question, re.I))
    return word.casefold() in question.casefold()


def time_window_expressed(question):
    # A requested bucket ("按日") does not specify which dates belong in the answer.
    grouping = r"按(?:季度|[日天周月季年])|每[日天周月季年]|逐[日天周月年]|\b(?:daily|weekly|monthly|yearly)\b|\bby\s+(?:day|week|month|quarter|year)\b"
    return guards.time_expressed(re.sub(grouping, " ", question, flags=re.I))


async def resolve_default(session, user, question, *, ver=None):
    """Return (published context, clarification/refusal). Explicit user time always wins."""
    ontology_id = require_ontology_id(session)
    if user.ontology_id != ontology_id:
        raise AppError("问数身份与工作空间不匹配", code="ONTOLOGY_SCOPE_CONFLICT", status_code=403)
    # Historical QA writes are blocked at the API; a direct caller must not borrow today's default.
    if ver:
        return None, {
            "term": "时间范围",
            "question": "历史口径不能借用当前指标默认时间，请明确时间与可执行口径",
            "options": ["指定时间范围", "使用当前已发布口径"],
        }
    if time_window_expressed(question):
        return None, None
    metrics = list(await session.scalars(select(Metric)))
    matches = []
    for metric in metrics:
        words = [metric.code, metric.api_name, metric.display_name, *meta_def(metric.def_rows)["synonyms"]]
        if any(_matches(question, word) for word in words):
            matches.append(metric)
    if not matches:
        return None, None
    try:
        check_func(user, "metrics", "view")
    except AppError:
        return None, {"code": "NO_PERMISSION", "reason": "当前身份无权使用指标默认时间范围"}
    matches = [metric for metric in matches if metric.status == MetricStatus.published]
    if not matches:
        return None, {"code": "NO_DATA", "reason": "该指标没有已发布口径，请先发布可执行定义或明确其他指标"}
    if len(matches) != 1:
        return None, {
            "term": "指标口径",
            "question": "该说法对应多个已发布指标，请选择明确口径及时间范围",
            "options": [f"{m.display_name}（{m.api_name}）" for m in matches[:6]],
        }
    metric = matches[0]
    value = exec_def(metric.def_rows).get("defaultRange")
    if value is None:
        return None, {
            "term": "时间范围",
            "question": f"{metric.display_name}未设置已发布默认时间范围，请指定时间",
            "options": ["今天", "本周", "本月", "指定其他时间范围"],
        }
    try:
        configured = default_range(value)
        objects = list(await session.scalars(select(ObjectType)))
        definition = {"formula": metric.formula, "def": metric.def_rows}
        bound, _, temporal, _, conditions = compile_metric(
            definition, objects, ontology_id, await execution_bindings(session, objects, definition)
        )
        if is_deny_all(user.row_filters.get(bound.object.code, {"deny": True})):
            raise AppError("当前身份无权查询该指标对象", code="NO_PERMISSION", status_code=403)
        direct_source = getattr(bound.governed, "binding", None) is not None
        if direct_source and not any(
            question.strip().rstrip("?？").casefold() == str(word).casefold()
            for word in (
                metric.code,
                metric.api_name,
                metric.display_name,
                *meta_def(metric.def_rows)["synonyms"],
            )
        ):
            raise AppError(
                "该来源当前仅支持直接提问已发布指标名称以计算默认范围汇总；额外条件或分组尚不可执行",
                code="METRIC_DEFAULT_UNAVAILABLE",
                status_code=422,
            )
        start, end, lo, hi = metric_window(configured["days"])
        if not isinstance(temporal.type, DateTime):
            lo, hi = lo.date(), hi.date()
        predicates = tuple(
            str(c.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
            for c in conditions
        )
        return PublishedRange(
            metric.code,
            metric.display_name,
            metric.version,
            bound.governed.table.name,
            temporal.name,
            configured["days"],
            str(start),
            str(end),
            lo.isoformat(),
            hi.isoformat(),
            predicates,
            str(metric_window(configured["days"])[2].tzinfo),
            direct_source,
        ), None
    except (ValueError, AppError) as error:
        reason = error.message if isinstance(error, AppError) else "默认时间范围配置不合法"
        return None, {
            "code": "NO_PERMISSION"
            if isinstance(error, AppError) and error.status_code == 403
            else "NO_DATA",
            "reason": f"{metric.display_name}的已发布默认不可执行：{reason}",
        }
