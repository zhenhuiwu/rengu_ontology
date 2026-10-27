"""决策评估（POST /decisions:evaluate）与决策记录（decision_log，只追加）。

只给建议：读取对象的未关闭告警 / 规则命中 / 生命周期，按元数据找候选动作（规则绑定的动作、
目标对象类型 = 该对象类型的已发布动作），预填参数、可选试运行（保存点内执行后一律回滚），
带上执行策略与发起检查，排序后写一条 decision_log。不暂存、不执行任何动作；人要执行时
照 recommendation.handoff 走 POST /action-runs:stage（人工确认）。

没有任何零售硬编码：参数预填只认三种元数据——规则 autoStage.mapping（告警.ID / 告警.对象 /
命中.对象 / 固定值：X / 告警.<维度类型名> / 算术表达式）、参数类型「对象引用 · <类型名>」、枚举参数的默认值。
数值参数的算术表达式（如「安全库存 × 1.5 − 可用库存」，映射里写的或参数默认值里写的）由 param_expr 安全
求值：名字先找告警触发时的取值（触发值「名：数」对、规则比较条件两侧对应的触发值 / 阈值），再找告警对象
及其维度对象的属性（显示名 / API 名，可写「类型名.属性」）；取不到、为空或不是数值时不填并给出原因。
"""

import json
import re
import secrets
from typing import Annotated, Any

from fastapi import Request
from pydantic import Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser, check_func
from app.common.context import ViewContext
from app.common.pagination import Page, PageParams
from app.core import clock
from app.core.errors import AppError, Forbidden, NotFound
from app.core.schema import ApiModel
from app.modules.action import policy as action_policy
from app.modules.action.models import ActionType, TypeStatus
from app.modules.dynamic import param_expr
from app.modules.dynamic.models import DecisionLog
from app.modules.dynamic.row_scope import scoped_instances
from app.modules.dynamic.rules_models import Rule
from app.modules.dynamic.service import AlertService, kind_label
from app.modules.dynamic.summary import ObjectDynamicSummary

Id64 = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[^\x00]+$")]
DRY_RUN_SCOPE = "actions:dry-run"
STAGE_PATH = "/api/ontology/v1/action-runs:stage"

# 排序分：触发来源取最高一项，同一动作每多一个来源 +3；试运行通过 +15、可发起 +10；
# 必填参数没填上每个 −5；执行策略不允许当前调用方 −40。
REASON_WEIGHT = {"alert": 100, "openAlert": 60, "ruleHit": 30, "lifecycle": 20, "objectType": 5}
LEVEL_BONUS = {"高": 10, "中": 5}
NUMERIC_TYPES = ("整数", "小数", "数值", "数字", "金额", "number", "integer", "decimal", "float")
ORDER_OPS = ("<", ">", "<=", ">=", "≤", "≥")
_NUM = re.compile(r"-?\d+(?:\.\d+)?")
_PAIR = re.compile(r"([^\s·：:]+)\s*[：:]\s*(-?\d+(?:\.\d+)?)(?![\d.])")


def _single_number(text: str | None) -> float | None:
    found = _NUM.findall(text or "")
    return float(found[0]) if len(found) == 1 else None


def alert_facts(alert: Any, conds: list | None) -> dict[str, dict[str, Any]]:
    """告警触发时的数值：触发值里的「名：数」对；规则只有一个比较条件时，
    条件左侧字段 ← 触发值里唯一的数、右侧（不是数字的名字）← 阈值里唯一的数。"""
    facts: dict[str, dict[str, Any]] = {}
    for label, num in _PAIR.findall(alert.value or ""):
        for name in {label, label.rsplit(".", 1)[-1]}:
            facts.setdefault(name, {"value": float(num), "from": f"告警触发值（{label}）"})
    ordering = [
        c
        for c in conds or []
        if isinstance(c, dict) and str(c.get("op") or "").strip() in ORDER_OPS and c.get("field")
    ]
    if len(ordering) == 1:
        cond = ordering[0]
        value, threshold = _single_number(alert.value), _single_number(alert.threshold)
        if value is not None:
            facts.setdefault(str(cond["field"]).strip(), {"value": value, "from": "告警触发值"})
        right = str(cond.get("value") or "").strip()
        if threshold is not None and right and _single_number(right) is None:
            facts.setdefault(right, {"value": threshold, "from": "告警阈值"})
    return facts


class DecisionCallerIn(ApiModel):
    """调用方自报的 AI 会话信息（只记录、不鉴权；身份以登录 / API 密钥为准）。"""

    ai_session_id: Id64 | None = None
    ai_round_id: Id64 | None = None
    agent: Annotated[str, Field(max_length=64)] | None = None


class DecisionEvaluateIn(ApiModel):
    alert_id: Id64 | None = None
    object_type_id: Id64 | None = None
    object_id: Id64 | None = None
    caller: DecisionCallerIn | None = None
    dry_run: bool = True
    limit: int = Field(5, ge=1, le=20)

    @model_validator(mode="after")
    def _one_target(self) -> "DecisionEvaluateIn":
        by_object = self.object_type_id is not None or self.object_id is not None
        if bool(self.alert_id) == by_object:
            raise ValueError("alertId 与 objectTypeId + objectId 二选一")
        if by_object and not (self.object_type_id and self.object_id):
            raise ValueError("objectTypeId 与 objectId 须同时提供")
        return self


def _can(user: CurrentUser, page: str, level: str) -> bool:
    try:
        check_func(user, page, level)
    except Forbidden:
        return False
    return True


class DecisionService:
    def __init__(
        self,
        session: AsyncSession,
        user: CurrentUser,
        catalog: Any,
        ports: Any,
        request: Request | None,
        *,
        system: dict[str, Any] | None = None,
    ):
        self.session = session
        self.user = user
        self.catalog = catalog
        self.ports = ports
        self.key = getattr(getattr(request, "state", None), "api_key", None)
        # 系统自动评估（告警产生后）：{"trigger": "rule", "ruleId": ...}；身份 user = 规则运行身份
        self.system = system

    # ---------------- 评估 ----------------

    async def evaluate(self, body: DecisionEvaluateIn) -> dict[str, Any]:
        if body.dry_run and self.key is not None and DRY_RUN_SCOPE not in self.key.scopes:
            raise Forbidden(
                "试运行需要 API 密钥范围 actions:dry-run（或传 dryRun=false）",
                details={"required": [DRY_RUN_SCOPE]},
            )
        alert = (
            await AlertService(self.session, self.user, self.catalog, self.ports).get(body.alert_id)
            if body.alert_id
            else None
        )
        kind = alert.obj_kind if alert else body.object_type_id
        obj_id = alert.obj_id if alert else body.object_id
        summary = await ObjectDynamicSummary(
            self.session,
            self.user,
            self.catalog,
            self.ports,
            api_scopes=self.key.scopes if self.key else None,
        ).get(kind, obj_id, limit=10)
        ctx = self._context(kind, obj_id, alert, summary)
        ctx["facts"] = alert_facts(alert, await self._conds(alert.rule_code)) if alert else {}
        triggers = self._triggers(alert, summary)
        notes: list[dict[str, Any]] = []
        candidates = await self._candidates(kind, triggers, summary, notes)
        is_ai = bool(body.caller and (body.caller.ai_session_id or body.caller.ai_round_id))
        recs = []
        for action, reasons, mapping in candidates.values():
            rec = await self._recommend(action, reasons, mapping, ctx, body.dry_run, is_ai, notes)
            if rec is not None:
                recs.append(rec)
        recs.sort(key=lambda r: (-r["score"], r["actionType"]["id"]))
        recs = recs[: body.limit]
        for i, rec in enumerate(recs, 1):
            rec["rank"] = i
        return await self._log(body, ctx, summary, recs, notes, is_ai)

    def _context(self, kind: str, obj_id: str, alert: Any, summary: dict) -> dict[str, Any]:
        dims = list(alert.dims or []) if alert else []
        if not dims:
            dims = next((list(x.get("dims") or []) for x in summary["alerts"].get("latest", [])), [])
        return {
            "objKind": kind,
            "objKindLabel": kind_label(self.catalog, kind),
            "objId": obj_id,
            "objLabel": alert.obj_label if alert else summary.get("objLabel") or obj_id,
            "alertId": alert.code if alert else None,
            "ruleId": alert.rule_code if alert else None,
            "dims": [
                {
                    "objKind": d.get("objKind"),
                    "id": d.get("id"),
                    "kindLabel": d.get("kindLabel") or kind_label(self.catalog, d.get("objKind") or ""),
                }
                for d in dims
                if isinstance(d, dict)
            ],
        }

    async def _conds(self, rule_code: str) -> list:
        conds = await self.session.scalar(select(Rule.conds).where(Rule.code == rule_code))
        return conds if conds is not None else (self.catalog.rule(rule_code) or {}).get("conds") or []

    def _triggers(self, alert: Any, summary: dict) -> list[tuple[str, dict[str, Any]]]:
        """(规则编号, 理由)：当前告警、对象其余未关闭告警、近期规则命中。"""
        out: list[tuple[str, dict[str, Any]]] = []
        if alert is not None:
            out.append(
                (
                    alert.rule_code,
                    {
                        "kind": "alert",
                        "alertId": alert.code,
                        "ruleId": alert.rule_code,
                        "level": alert.level.value,
                        "text": f"当前告警 {alert.code} 由规则 {alert.rule_code} 触发（{alert.level.value}）",
                    },
                )
            )
        for x in summary["alerts"].get("latest", []):
            if alert is not None and x.get("id") == alert.code:
                continue
            out.append(
                (
                    x.get("ruleId"),
                    {
                        "kind": "openAlert",
                        "alertId": x.get("id"),
                        "ruleId": x.get("ruleId"),
                        "level": x.get("level"),
                        "text": f"对象另有未关闭告警 {x.get('id')}"
                        f"（规则 {x.get('ruleId')}，{x.get('status')}）",
                    },
                )
            )
        for h in summary["ruleHits"].get("latest", []):
            out.append(
                (
                    h.get("ruleId"),
                    {
                        "kind": "ruleHit",
                        "hitId": h.get("id"),
                        "ruleId": h.get("ruleId"),
                        "text": f"规则 {h.get('ruleId')} 近期命中 {h.get('id')}（{h.get('result')}）",
                    },
                )
            )
        return [(rule, reason) for rule, reason in out if rule]

    async def _rule_action(self, rule: Rule | None, code: str) -> tuple[str | None, list]:
        """规则 → 动作类型编号：Rule.action_type_id → autoStage.actionTypeId
        → 目录里的规则动作（编号或名称）。"""
        auto = (rule.auto_stage if rule else None) or {}
        mapping = [m for m in auto.get("mapping") or [] if isinstance(m, dict)]
        if rule is not None and rule.action_type_id:
            action = await self.session.scalar(
                select(ActionType.code).where(ActionType.id == rule.action_type_id)
            )
            if action:
                return action, mapping
        if auto.get("actionTypeId"):
            return str(auto["actionTypeId"]), mapping
        seed = self.catalog.rule(code) or {}
        if seed.get("actionTypeId"):
            return str(seed["actionTypeId"]), mapping
        named = self.catalog.action_type_by_name(seed["action"]) if seed.get("action") else None
        return (named or {}).get("id"), mapping

    async def _candidates(
        self, kind: str, triggers: list, summary: dict, notes: list
    ) -> dict[str, tuple[ActionType, list[dict], list]]:
        live = (
            ActionType.retired_release_id.is_(None),
            ActionType.status.in_((TypeStatus.published, TypeStatus.changing)),
        )
        rule_codes = {rule for rule, _ in triggers}
        rules = (
            {r.code: r for r in await self.session.scalars(select(Rule).where(Rule.code.in_(rule_codes)))}
            if rule_codes
            else {}
        )
        found: dict[str, tuple[list[dict], list]] = {}
        unbound: set[str] = set()
        for rule_code, reason in triggers:
            action_code, mapping = await self._rule_action(rules.get(rule_code), rule_code)
            if not action_code:
                unbound.add(rule_code)
                continue
            reasons, old = found.setdefault(action_code, ([], mapping))
            reasons.append(reason)
            if not old and mapping:
                found[action_code] = (reasons, mapping)
        notes += [
            {"kind": "ruleWithoutAction", "ruleId": r, "text": f"规则 {r} 未绑定动作"}
            for r in sorted(unbound)
        ]
        typed = list(
            await self.session.scalars(select(ActionType).where(ActionType.target_kind == kind, *live))
        )
        lifecycle = summary.get("lifecycle") or {}
        illegal = int(lifecycle.get("illegalTotal") or 0) if lifecycle.get("defined") else 0
        for action in typed:
            reasons, _ = found.setdefault(action.code, ([], []))
            reasons.append(
                {"kind": "objectType", "text": f"动作目标对象类型为 {kind_label(self.catalog, kind)}"}
            )
            if illegal:
                reasons.append(
                    {
                        "kind": "lifecycle",
                        "state": (lifecycle.get("current") or {}).get("state"),
                        "text": f"生命周期有 {illegal} 次未登记迁移",
                    }
                )
        actions = (
            {
                a.code: a
                for a in await self.session.scalars(
                    select(ActionType).where(ActionType.code.in_(found), *live)
                )
            }
            if found
            else {}
        )
        out = {}
        for code, (reasons, mapping) in found.items():
            if code in actions:
                out[code] = (actions[code], reasons, mapping)
            else:
                notes.append(
                    {
                        "kind": "actionUnavailable",
                        "actionTypeId": code,
                        "text": f"规则绑定的动作 {code} 不存在、未发布或已退役",
                    }
                )
        return out

    # ---------------- 单个建议 ----------------

    async def _recommend(
        self,
        action: ActionType,
        reasons: list,
        mapping: list,
        ctx: dict,
        dry_run: bool,
        is_ai: bool,
        notes: list,
    ) -> dict[str, Any] | None:
        from app.modules.action.checker import definition as action_definition

        try:
            defn = await action_definition(self.session, action.code, ViewContext(), "published", None)
        except AppError as exc:
            notes.append({"kind": "actionUnavailable", "actionTypeId": action.code, "text": exc.message})
            return None
        same_kind = (defn.get("targetKind") or action.target_kind) == ctx["objKind"]
        params, sources, unfilled = await self._prefill(defn.get("params") or [], mapping, ctx, same_kind)
        dry = (
            await self._dry_run(action.code, defn, params, ctx, same_kind)
            if dry_run
            else {"ran": False, "reason": "not-requested"}
        )
        availability = await self._availability(action.code)
        pol = await action_policy.load(self.session, action.code, action.version)
        caller_type = action_policy.caller_of(
            "规则" if self.system else "问数" if is_ai else "手动", self.user
        )
        caller_allowed = pol.callers is None or caller_type in pol.callers
        policy = {
            "mode": pol.mode,
            "callers": pol.callers,
            "callerType": caller_type,
            "callerAllowed": caller_allowed,
            "limits": pol.limits.model_dump(mode="json", by_alias=True) if pol.limits else None,
        }
        score = max(REASON_WEIGHT[r["kind"]] + LEVEL_BONUS.get(r.get("level") or "", 0) for r in reasons)
        score += (
            3 * (len(reasons) - 1)
            + (15 if dry.get("passed") else 0)
            + (10 if availability.get("available") else 0)
        )
        score -= 5 * len(unfilled) + (0 if caller_allowed else 40)
        return {
            "rank": 0,
            "score": score,
            "actionType": {
                "id": action.code,
                "name": action.name,
                "version": action.version,
                "targetKind": action.target_kind,
            },
            "reasons": reasons,
            "params": params,
            "paramSources": sources,
            "unfilledParams": unfilled,
            "dryRun": dry,
            "availability": availability,
            "policy": policy,
            "handoff": {
                "method": "POST",
                "path": STAGE_PATH,
                "requiresHumanConfirmation": True,
                "body": {
                    "typeId": action.code,
                    "source": {"type": "手动"},
                    "objKind": ctx["objKind"] if same_kind else None,
                    "objId": ctx["objId"] if same_kind else None,
                    "objLabel": ctx["objLabel"] if same_kind else None,
                    "params": json.dumps(params, ensure_ascii=False),
                },
            },
        }

    def _source(self, src: str, ctx: dict) -> str | None:
        src = src.strip()
        if src == "告警.ID":
            return ctx["alertId"]
        if src in ("告警.对象", "命中.对象"):
            return ctx["objId"]
        for prefix in ("固定值：", "固定值:"):
            if src.startswith(prefix):
                return src[len(prefix) :].strip() or None
        if src.startswith("告警."):
            return self._ref(src[3:], ctx)
        return None

    @staticmethod
    def _ref(label: str, ctx: dict) -> str | None:
        label = label.strip()
        if label in (ctx["objKindLabel"], ctx["objKind"]):
            return ctx["objId"]
        if label == "告警":
            return ctx["alertId"]
        return next((d["id"] for d in ctx["dims"] if label in (d["kindLabel"], d["objKind"])), None)

    async def _prefill(
        self, params: list, mapping: list, ctx: dict, same_kind: bool
    ) -> tuple[dict, dict, list]:
        by_name = {m.get("param"): m.get("from") for m in mapping if m.get("param") and m.get("from")}
        values: dict[str, Any] = {}
        sources: dict[str, str] = {}
        unfilled = []
        for p in params:
            api, name, ptype = p.get("api"), p.get("name"), str(p.get("type") or "")
            if not api:
                continue
            mapped = by_name.get(name) or by_name.get(api)
            value, source, reason = None, None, None
            if mapped:
                value, source = self._source(str(mapped), ctx), f"规则映射：{mapped}"
            numeric = any(t in ptype.lower() for t in NUMERIC_TYPES)
            if value is None and numeric:
                for text, label in ((mapped, "规则映射"), (p.get("def"), "参数默认值")):
                    if not text or value is not None:
                        continue
                    res = await self._expression(str(text), ctx)
                    if res["ok"]:
                        value = param_expr.coerce(res["value"], ptype)
                        inputs = "，".join(
                            f"{x['name']}={x['value']:g}（{x['from']}）" for x in res["inputs"]
                        )
                        source = f"{label}表达式：{text}" + (f"；{inputs}" if inputs else "")
                    elif res.get("expression") and (reason is None or text == mapped):
                        reason = f"{label}表达式「{text}」未求值：{res['reason']}"
            ref = re.match(r"对象引用\s*[·•]\s*(.+)$", ptype)
            if value is None and ref:
                value, source = self._ref(ref.group(1), ctx), f"参数类型：{ptype}"
            if value is None and ptype == "枚举" and p.get("def"):
                options = [o for o in re.split(r"\s*[/／、,，]\s*", str(p.get("rule") or "")) if o]
                if p["def"] in options:
                    value, source = p["def"], "参数默认值"
            if value is not None:
                values[api], sources[api] = value, source
            elif p.get("required"):
                row = {"api": api, "name": name, "type": ptype, "default": p.get("def"), "mapping": mapped}
                if reason:
                    row["reason"] = reason
                unfilled.append(row)
        return values, sources, unfilled

    async def _expression(self, text: str, ctx: dict) -> dict[str, Any]:
        """表达式里的名字：告警触发时的取值 → 告警对象属性 → 维度对象属性（可写「类型名.属性」）。"""
        targets = [(ctx["objKind"], ctx["objKindLabel"], ctx["objId"])] + [
            (d["objKind"], d["kindLabel"], d["id"]) for d in ctx["dims"] if d.get("objKind") and d.get("id")
        ]
        rows = {}
        for kind, _, oid in targets:
            if (kind, oid) not in rows:
                rows[(kind, oid)] = await self._object_values(kind, oid)

        def resolve(name: str) -> tuple[bool, Any, str | None]:
            fact = (ctx.get("facts") or {}).get(name)
            if fact is not None:
                return True, fact["value"], fact["from"]
            prefix, _, prop = name.rpartition(".")
            for kind, label, oid in targets:
                if prefix and prefix not in (kind, label):
                    continue
                values = rows.get((kind, oid)) or {}
                if prop in values:
                    return True, values[prop], f"{label} {oid}.{prop}"
            return False, None, None

        return param_expr.evaluate(text, resolve)

    async def _object_values(self, kind: str, obj_id: str) -> dict[str, Any]:
        """对象当前属性值，键同时含 API 名与显示名；未接入 / 无权限 / 数据源不可用 → 空。"""
        from app.modules.qa.explore_api import _bind

        try:
            _, bound = await _bind(self.session, kind)
            if bound is None:
                return {}
            stmt = (await bound.stmt(self.user)).where(bound.id_col == bound.id_value(obj_id))
            async with bound.read(self.session) as reader:
                row = (await reader.execute(stmt.limit(1))).mappings().first()
        except AppError:
            return {}
        if row is None:
            return {}
        out: dict[str, Any] = {}
        for api, value in row.items():
            out[api] = value
            label = getattr(bound.props.get(api), "display_name", None)
            if label:
                out.setdefault(label, value)
        return out

    async def _isolated(self, run):
        """保存点内执行、一律回滚：试运行 / 发起检查都不在库里留下任何东西。"""
        savepoint = await self.session.begin_nested()
        try:
            return await run()
        finally:
            if savepoint.is_active:
                await savepoint.rollback()

    async def _dry_run(
        self, code: str, defn: dict, params: dict, ctx: dict, same_kind: bool
    ) -> dict[str, Any]:
        from app.modules.action.checker import DatabaseSubmissionChecker

        if not _can(self.user, "action-types", "view"):
            return {"ran": False, "reason": "no-permission", "required": "action-types:view"}
        if self.key is not None and self.key.action_types is not None and code not in self.key.action_types:
            return {"ran": False, "reason": "key-allowlist"}
        context = {
            "params": params,
            "objKind": ctx["objKind"] if same_kind else None,
            "objId": ctx["objId"] if same_kind else None,
            "alertId": ctx["alertId"],
        }
        try:
            result = await self._isolated(
                lambda: DatabaseSubmissionChecker(self.session, self.user).run(defn, context)
            )
        except AppError as exc:
            return {"ran": True, "passed": False, "error": {"code": exc.code, "message": exc.message}}
        return {"ran": True, **result}

    async def _availability(self, code: str) -> dict[str, Any]:
        from app.modules.dynamic.action_options import launch_definition

        if not _can(self.user, "action-center", "manage"):
            return {"checked": False, "reason": "no-permission", "required": "action-center:manage"}
        try:
            await self._isolated(lambda: launch_definition(self.session, self.user, self.catalog, code))
        except AppError as exc:
            return {"checked": True, "available": False, "reason": exc.message, "code": exc.code}
        return {"checked": True, "available": True}

    # ---------------- 记录 ----------------

    def _caller(self, body: DecisionEvaluateIn, is_ai: bool) -> dict[str, Any]:
        c = body.caller
        if self.system:
            kind = "system"
        elif is_ai:
            kind = "ai"
        else:
            kind = "service_account" if self.user.is_service_account else "user"
        return {
            "kind": kind,
            "system": self.system,
            "id": self.user.id,
            "name": self.user.name,
            "principalKind": self.key.principal_kind if self.key else "user",
            "serviceAccount": self.key.service_account_code if self.key else None,
            "apiKeyPrefix": self.key.prefix if self.key else None,
            "aiSessionId": c.ai_session_id if c else None,
            "aiRoundId": c.ai_round_id if c else None,
            "agent": c.agent if c else None,
        }

    async def _log(self, body, ctx, summary, recs, notes, is_ai) -> dict[str, Any]:
        caller = self._caller(body, is_ai)
        signals = {
            "alerts": {k: summary["alerts"].get(k) for k in ("available", "reason", "openCount", "byStatus")},
            "ruleHits": {k: summary["ruleHits"].get(k) for k in ("available", "reason", "total")},
            "lifecycle": {
                k: summary["lifecycle"].get(k)
                for k in ("available", "reason", "defined", "lifecycleId", "current", "illegalTotal")
            },
        }
        now = clock.now()
        row = DecisionLog(
            code=f"DEC-{now:%Y%m%d%H%M%S}-{secrets.token_hex(3)}",
            caller_kind=caller["kind"],
            caller_id=caller["id"],
            caller_name=(f"规则 {self.system.get('ruleId')} 自动评估" if self.system else caller["name"])[
                :64
            ],
            api_key_prefix=caller["apiKeyPrefix"],
            ai_session_id=caller["aiSessionId"],
            ai_round_id=caller["aiRoundId"],
            obj_kind=ctx["objKind"],
            obj_id=ctx["objId"],
            alert_code=ctx["alertId"],
            input={
                "request": body.model_dump(mode="json", by_alias=True),
                "caller": caller,
                "context": ctx,
                "signals": signals,
            },
            output={"suggestOnly": True, "recommendations": recs, "notes": notes},
            created_by=self.user.id,
            created_at=now,
        )
        self.session.add(row)
        await self.session.flush()
        return self.out(row)

    # ---------------- 读取 ----------------

    @staticmethod
    def out(row: DecisionLog, *, full: bool = True) -> dict[str, Any]:
        caller = (row.input or {}).get("caller") or {}
        recs = (row.output or {}).get("recommendations") or []
        base = {
            "decisionId": row.code,
            "createdAt": row.created_at.isoformat(),
            "caller": caller,
            "objKind": row.obj_kind,
            "objId": row.obj_id,
            "alertId": row.alert_code,
            "suggestOnly": True,
            "error": (row.output or {}).get("error"),
        }
        if not full:
            top = recs[0]["actionType"] if recs else None
            return {**base, "recommendationCount": len(recs), "top": top}
        ctx = row.input or {}
        return {
            **base,
            "context": ctx.get("context"),
            "signals": ctx.get("signals"),
            "request": ctx.get("request"),
            "recommendations": recs,
            "notes": (row.output or {}).get("notes") or [],
        }

    async def _scoped(self, stmt):
        return await scoped_instances(stmt, self.user, DecisionLog.obj_kind, DecisionLog.obj_id)

    async def list(
        self,
        params: PageParams,
        *,
        obj_kind: str | None,
        obj_id: str | None,
        alert_id: str | None,
        caller_kind: str | None,
        ai_session_id: str | None,
    ) -> Page[dict[str, Any]]:
        stmt = select(DecisionLog)
        for column, value in (
            (DecisionLog.obj_kind, obj_kind),
            (DecisionLog.obj_id, obj_id),
            (DecisionLog.alert_code, alert_id),
            (DecisionLog.caller_kind, caller_kind),
            (DecisionLog.ai_session_id, ai_session_id),
        ):
            if value:
                stmt = stmt.where(column == value)
        stmt = await self._scoped(stmt)
        total = int(await self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
        p = params.clamp(total)
        rows = await self.session.scalars(
            stmt.order_by(DecisionLog.created_at.desc(), DecisionLog.id.desc()).offset(p.offset).limit(p.size)
        )
        return Page[dict[str, Any]].of([self.out(r, full=False) for r in rows], total, params)

    async def latest_for_alert(self, code: str) -> dict[str, Any] | None:
        """告警可见才返回（AlertService.get 做行级可见性）；没有决策记录 → None。"""
        await AlertService(self.session, self.user, self.catalog, self.ports).get(code)
        row = await self.session.scalar(
            (await self._scoped(select(DecisionLog).where(DecisionLog.alert_code == code)))
            .order_by(DecisionLog.created_at.desc(), DecisionLog.id.desc())
            .limit(1)
        )
        return self.out(row) if row is not None else None

    async def get(self, code: str) -> dict[str, Any]:
        row = await self.session.scalar(
            await self._scoped(select(DecisionLog).where(DecisionLog.code == code))
        )
        if row is None:
            raise NotFound("决策记录不存在或无权查看", details={"decisionId": code})
        return self.out(row)
