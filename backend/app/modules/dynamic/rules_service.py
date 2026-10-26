"""规则与自动化 / 情景推演 / 生命周期的业务逻辑（dynamic-api.md §2.3–2.5、§4、§6.3–6.5）。

- 出参是前端 mock 形状的 dict（RuleDef、Hit、Scenario、Lifecycle…）；分页用骨架 Page.of；
- 回放试跑照搬 rules.ts replay()（同一伪随机序列），不写任何业务表；
- 推演计算 = rules_sim.simulate（示意模型，逐位等于 mock）；运行在请求内同步算完 [推断]；
- 变更单表 master 上还没有：规则 / 生命周期草稿暂存在 rule_draft / lifecycle_draft（cr_code 软引用）[推断]。
"""

import json
import math
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.auth import CurrentUser
from app.common.pagination import Page, PageParams
from app.core.clock import now
from app.core.errors import AppError, NotFound
from app.core.schema import _fmt_dt, _fmt_dt_seconds, parse_display_dt
from app.modules.action.models import ActionType
from app.modules.dynamic.cr_link import CrLink, lifecycle_sections, rule_sections
from app.modules.dynamic.models import ActionRun, ActionStatus, Alert, AlertLevel
from app.modules.dynamic.rules_models import (
    HitResult,
    Lifecycle,
    LifecycleChange,
    LifecycleDraft,
    LifecycleTransition,
    LifecycleVersion,
    Rule,
    RuleDraft,
    RuleHit,
    RuleOutput,
    RuleStatus,
    Scenario,
    ScenarioImpact,
    ScenarioResult,
    ScenarioStatus,
)
from app.modules.dynamic.rules_schemas import (
    DriversIn,
    LifecycleDraftIn,
    ReplayIn,
    RuleCreateIn,
    RuleDraftIn,
    ScenarioRunIn,
    ScopeIn,
)
from app.modules.dynamic.rules_sim import _products, _stores, seeded, simulate, stage_plan, to_fixed_str
from app.modules.dynamic.sections import lifecycle_def, lifecycle_head, rule_def, version_row
from app.modules.static.models import ObjectType
from app.modules.static.schemas import TargetIn
from app.modules.versioning.models import OntologyRelease

# 前端写死的「今天」口径（rules.ts / lifecycle.ts）
CUT_7D = "2026-10-18 23:59:59"
CUT_30D = "2026-09-25 00:00:00"
SINCE_7D = "2026-10-19"
_JSON = Path(__file__).resolve().parents[3] / "seeds" / "json" / "dynamic"


@cache
def _meta(name: str) -> Any:
    """页面下拉选项（mock 常量 CONFIRMERS / MAPPING_SOURCES / SC_AREAS / SC_CATEGORIES / PROMO_TYPES）。
    EXTENSION POINT (PDE): 改成字典表后从库里读。"""
    return json.loads((_JSON / f"{name}.json").read_text(encoding="utf-8"))


def fail(code: str, message: str, status: int, **details: Any) -> AppError:
    return AppError(message, code=code, status_code=status, details=details or None)


def validation(fields: list[tuple[str, str]]) -> AppError:
    return AppError(
        "参数校验失败",
        code="VALIDATION_FAILED",
        status_code=422,
        details={"fields": [{"field": f, "message": m} for f, m in fields]},
    )


def paginate(items: list[Any], params: PageParams) -> Page[dict[str, Any]]:
    p = params.clamp(len(items))
    return Page[dict[str, Any]].of(items[p.offset : p.offset + p.size], len(items), params)


def sec(dt: datetime) -> str:
    return _fmt_dt_seconds(dt)


# ======================================================================
# 规则
# ======================================================================


def dedupe_reason(d: dict[str, Any]) -> str:
    """rules.ts dedupeReason。"""
    dd, cd = d["dedupe"], d["cooldown"]
    if "事件" in dd or "单号" in dd:
        return f"同一{dd.replace(' ID', '', 1)}已命中过"
    skip = {"规则", "日期", "周", "小时", "生效日期", "批次"}
    dims = "".join(x.strip() for x in dd.split("+") if x.strip() not in skip)
    return f"同一{dims}已命中过" if cd == "不重复" else f"同一{dims} {cd}内已命中"


def hit_dims(store_id: str | None, store_label: str | None) -> list[dict[str, Any]]:
    """命中的上下文维度（通用 dims，替代 storeId / storeLabel）。
    rule_hit.store_id / store_label 两列保留（deprecated，不加迁移），这里映射成一条 objKind=store 的维度。"""
    return [{"objKind": "store", "id": store_id, "label": store_label or store_id}] if store_id else []


def store_of_key(key: str) -> dict[str, Any]:
    h = 7
    for c in key:
        h = (h * 31 + ord(c)) % 100003
    stores = _stores()
    return stores[h % len(stores)]


class RuleService:
    def __init__(self, session: AsyncSession, user: CurrentUser) -> None:
        self.session = session
        self.user = user

    # ---------- 读 ----------
    async def _actions(self) -> dict[int, str]:
        return {i: c for i, c in (await self.session.execute(select(ActionType.id, ActionType.code))).all()}

    async def get(self, code: str) -> Rule:
        r = await self.session.scalar(select(Rule).where(Rule.code == code))
        if r is None:
            raise NotFound("规则不存在", details={"id": code})
        return r

    async def _drafts(self) -> dict[int, RuleDraft]:
        rows = (await self.session.scalars(select(RuleDraft).order_by(RuleDraft.id))).all()
        return {d.rule_id: d for d in rows}

    @staticmethod
    def display_status(r: Rule, d: RuleDraft | None) -> dict[str, Any]:
        """规则状态取自所在变更单：CR 草稿 → 草稿中；审核中 → 审核中（CR 表接入前一律草稿中 [推断]）。"""
        if d is None:
            return {"text": "草稿中" if r.status == RuleStatus.changing else r.status.value, "cr": None}
        return {"text": "草稿中", "cr": d.cr_code}

    def _def(self, r: Rule, actions: dict[int, str]) -> dict[str, Any]:
        return rule_def(r, actions.get(r.action_type_id) if r.action_type_id else None)

    async def list_rules(
        self, params: PageParams, *, trigger: str | None, status: str | None, kw: str | None
    ) -> Page[dict[str, Any]]:
        actions, drafts = await self._actions(), await self._drafts()
        rows = (
            await self.session.scalars(
                select(Rule)
                .where(or_(Rule.version.is_not(None), Rule.id.in_(list(drafts))))
                .order_by(Rule.id)
            )
        ).all()
        out = []
        for r in rows:
            st = self.display_status(r, drafts.get(r.id))
            if trigger and trigger != "全部" and r.trigger.get("kind") != trigger:
                continue
            if status and status != "全部" and st["text"] != status:
                continue
            if kw and kw not in r.name and kw.lower() not in r.code.lower():
                continue
            out.append({**self._def(r, actions), "displayStatus": st["text"], "cr": st["cr"]})
        return paginate(out, params)

    async def meta(self) -> dict[str, Any]:
        rows = (
            await self.session.scalars(select(Rule).where(Rule.version.is_not(None)).order_by(Rule.id))
        ).all()
        drafts = await self._drafts()
        statuses = list(dict.fromkeys(self.display_status(r, drafts.get(r.id))["text"] for r in rows))
        return {
            "confirmers": _meta("CONFIRMERS"),
            "mappingSources": _meta("MAPPING_SOURCES"),
            "triggers": ["全部", "定时", "事件"],
            "statuses": ["全部", *statuses],
        }

    async def draft_def(self, r: Rule, d: RuleDraft | None, actions: dict[int, str]) -> dict[str, Any] | None:
        if d is None:
            return None
        return {**self._def(r, actions), **d.patch, "version": d.version}

    async def detail(self, code: str, *, view: str | None) -> dict[str, Any]:
        r = await self.get(code)
        actions = await self._actions()
        d = (await self._drafts()).get(r.id)
        base = self._def(r, actions)
        draft = await self.draft_def(r, d, actions)
        st = self.display_status(r, d)
        hits7 = await self.session.scalar(
            select(func.count())
            .select_from(RuleHit)
            .where(RuleHit.rule_id == r.id, RuleHit.at > parse_display_dt(CUT_7D))
        )
        pending = await self.session.scalar(
            select(func.count())
            .select_from(ActionRun)
            .where(ActionRun.rule_code == r.code, ActionRun.status == ActionStatus.staged)
        )
        return {
            **base,
            "displayStatus": st["text"],
            "cr": st["cr"],
            "draft": draft,
            "view": "draft" if view == "draft" and draft else "published",
            "editable": view == "draft" and draft is not None,
            "editTip": "" if view == "draft" and draft else "已发布版本只读，请先发起变更",
            "dedupeReason": dedupe_reason(base),
            "hits7d": hits7,
            "stagedPending": pending,
        }

    async def _hits(self, r: Rule) -> list[RuleHit]:
        return list(
            (
                await self.session.scalars(
                    select(RuleHit).where(RuleHit.rule_id == r.id).order_by(RuleHit.seq)
                )
            ).all()
        )

    async def _alerts(self, ids: set[int]) -> dict[int, Alert]:
        if not ids:
            return {}
        return {a.id: a for a in (await self.session.scalars(select(Alert).where(Alert.id.in_(ids)))).all()}

    @staticmethod
    def hit_out(h: RuleHit, rule_code: str, alert: Alert | None) -> dict[str, Any]:
        """Hit（rules.ts）：告警命中的 result = 告警当前状态；其余 = 已通知 / 已去重。"""
        return {
            "id": h.code,
            "ruleId": rule_code,
            "at": sec(h.at),
            "objKind": h.obj_kind,
            "objId": h.obj_id,
            "objLabel": h.obj_label,
            "dims": hit_dims(h.store_id, h.store_label),
            "value": h.value,
            "threshold": h.threshold,
            "alertId": alert.code if alert else None,
            "result": alert.status.value if (alert and h.result == HitResult.alert) else h.result.value,
        }

    async def hit_dicts(self, r: Rule) -> list[dict[str, Any]]:
        hits = await self._hits(r)
        alerts = await self._alerts({h.alert_id for h in hits if h.alert_id})
        return [self.hit_out(h, r.code, alerts.get(h.alert_id) if h.alert_id else None) for h in hits]

    async def hits(self, code: str, params: PageParams, *, range_: str) -> Page[dict[str, Any]]:
        r = await self.get(code)
        cut = CUT_7D if range_ == "7d" else "2026-09-25"
        return paginate([h for h in await self.hit_dicts(r) if h["at"] > cut], params)

    async def hit(self, code: str) -> tuple[Rule, RuleHit, dict[str, Any], Alert | None]:
        h = await self.session.scalar(select(RuleHit).where(RuleHit.code == code))
        if h is None:
            raise NotFound("命中记录不存在", details={"id": code})
        r = await self.session.get(Rule, h.rule_id)
        assert r is not None
        alert = (await self._alerts({h.alert_id})).get(h.alert_id) if h.alert_id else None
        return r, h, self.hit_out(h, r.code, alert), alert

    async def versions(self, code: str) -> list[dict[str, Any]]:
        from app.modules.dynamic.rules_models import RuleVersion

        r = await self.get(code)
        d = (await self._drafts()).get(r.id)
        rows = (
            await self.session.scalars(
                select(RuleVersion).where(RuleVersion.rule_id == r.id).order_by(RuleVersion.version.desc())
            )
        ).all()
        out = []
        if d is not None:
            out.append(
                {
                    "ver": d.version,
                    "status": "草稿",
                    "by": d.by,
                    "at": _fmt_dt(d.at) if d.at else None,
                    "change": d.change,
                    "cr": d.cr_code,
                }
            )
        for v in rows:
            out.append(
                {
                    "ver": v.version,
                    "status": "生效中" if v.version == r.version else "已替代",
                    "by": v.by,
                    "at": _fmt_dt(v.at),
                    "change": v.change,
                }
            )
        return out

    async def replay(self, code: str, body: ReplayIn) -> dict[str, Any]:
        """rules.ts replay()：不落业务数据。"""
        r = await self.get(code)
        actions = await self._actions()
        d = (await self._drafts()).get(r.id)
        use_draft = body.version == "draft"
        if use_draft and d is None:
            raise validation([("version", "这条规则没有草稿，不能按草稿试跑")])
        rule = (await self.draft_def(r, d, actions)) if use_draft else self._def(r, actions)
        assert rule is not None
        cut = CUT_7D if body.range == "7d" else CUT_30D
        all_ = [h for h in await self.hit_dicts(r) if h["at"] > cut]
        counted = [h for h in all_ if h["result"] != "已去重"]
        rnd = seeded(len(all_) * 13 + (7 if use_draft else 1) + (3 if body.range == "7d" else 5))
        samples = []
        for h in all_:
            if h["result"] == "已去重":
                diff = "被去重"
            elif use_draft and not rule["core"] and rnd() < 0.18:
                diff = "不再命中"
            else:
                diff = "一致"
            samples.append(
                {
                    "key": h["id"],
                    "at": h["at"],
                    "objKind": h["objKind"],
                    "objId": h["objId"],
                    "objLabel": h["objLabel"],
                    "dims": h["dims"],
                    "value": h["value"],
                    "diff": diff,
                }
            )
        if use_draft:
            extra = max(2, math.floor(len(counted) * 0.12 + 0.5))
            products = _products()
            for i in range(extra):
                if not counted:
                    break
                h = counted[math.floor(rnd() * max(1, len(counted)))]
                used = {x["objId"] for x in samples}
                is_p = rule["objKind"] == "product"
                p = next((x for x in products if x["sku"] not in used), products[0]) if is_p else None
                st = store_of_key(f"{rule['id']}-NEW-{i}") if is_p else None
                samples.append(
                    {
                        "key": f"NEW-{i}",
                        "at": h["at"],
                        "objKind": h["objKind"],
                        "objId": p["sku"] if p else h["objId"],
                        "objLabel": f"{p['name']} {p['spec']}" if p else h["objLabel"],
                        "dims": hit_dims(st["id"], st["name"]) if st else h["dims"],
                        "value": f"{to_fixed_str(17 + rnd(), 2)}%" if is_p else h["value"],
                        "diff": "新增命中",
                    }
                )
        samples.sort(key=lambda s: s["at"], reverse=True)
        n = {k: sum(1 for s in samples if s["diff"] == k) for k in ("一致", "新增命中", "不再命中", "被去重")}
        return {
            "actual": len(counted),
            "would": n["一致"] + n["新增命中"],
            "added": n["新增命中"],
            "removed": n["不再命中"],
            "deduped": n["被去重"],
            "dedupeReason": dedupe_reason(rule),
            "samples": samples,
        }

    async def _next_code(self) -> str:
        codes = (await self.session.scalars(select(Rule.code))).all()
        n = max((int(c[2:]) for c in codes if c.startswith("R-") and c[2:].isdigit()), default=0)
        return f"R-{n + 1:03d}"

    async def create(self, body: RuleCreateIn) -> dict[str, Any]:
        """新建规则：未发布（version=null，不进快照），状态变更中；传 cr 时同时开草稿（v1，改动为空）。"""
        name = (body.name or "").strip() or "未命名规则"
        r = Rule(
            code=await self._next_code(),
            version=None,
            name=name,
            desc="",
            owner=self.user.name,
            status=RuleStatus.changing,
            core=False,
            obj_kind=body.obj_kind or await self._first_obj_kind(),
            level=AlertLevel.medium,
            output=RuleOutput.alert,
            conds=[],
            logic="且",
            trigger={"kind": "定时", "cron": "0 8 * * *", "text": "每天 08:00"},
            auto_stage={"enabled": False, "actionTypeId": "", "mapping": [], "confirmer": ""},
            is_seed=False,
        )
        self.session.add(r)
        await self.session.flush()
        if body.cr:
            await self._open(r, body.cr)
            cur = await self.draft_def(r, (await self._drafts())[r.id], await self._actions())
            assert cur is not None
            await CrLink(self.session, self.user).put(
                body.cr, self._target(r), {"config": rule_sections(None, cur)["config"]}, None
            )
        return await self.detail(r.code, view="draft" if body.cr else None)

    async def _first_obj_kind(self) -> str:
        """新建规则默认对象类型 = 本体里第一个对象类型（表空时退回 product）。"""
        return (
            await self.session.scalar(select(ObjectType.code).order_by(ObjectType.id).limit(1)) or "product"
        )

    @staticmethod
    def _target(r: Rule) -> TargetIn:
        return TargetIn(kind="rule", id=r.code, is_new=r.version is None, name=r.name)

    async def _open(self, r: Rule, cr: str) -> RuleDraft:
        d = (await self._drafts()).get(r.id)
        if d is not None:
            if d.cr_code != cr:
                msg = f"该规则已在变更单 {d.cr_code} 的草稿里"
                raise fail("TARGET_IN_OTHER_CR", msg, 409, crId=d.cr_code)
            return d
        d = RuleDraft(rule_id=r.id, cr_code=cr, version=(r.version or 0) + 1, patch={}, by=self.user.name)
        self.session.add(d)
        if r.status != RuleStatus.disabled:  # 停用规则开草稿不改状态，放弃后仍是停用（不被误启用）
            r.status = RuleStatus.changing
        await self.session.flush()
        return d

    async def open_draft(self, code: str, cr: str) -> dict[str, Any]:
        """发起变更 / 加入已有变更单：登记规则草稿（幂等；已在别的草稿单里 409）。"""
        r = await self.get(code)
        await self._open(r, cr)
        await CrLink(self.session, self.user).join(cr, self._target(r))
        return await self.detail(code, view="draft")

    async def discard_draft(self, code: str, cr: str | None) -> None:
        """放弃本规则草稿：删草稿行；从未发布过的新规则连同规则一起删。"""
        r = await self.get(code)
        d = (await self._drafts()).get(r.id)
        if d is None or (cr and cr != d.cr_code):
            raise NotFound("该规则没有草稿", details={"id": code, "cr": cr})
        # 同一事务：先删变更单里的登记和草稿行（单子已提交 → CR_LOCKED，整体回滚，不留孤儿），再删规则草稿
        await CrLink(self.session, self.user).discard(d.cr_code, "rule", r.code)
        await self.session.delete(d)
        if r.version is None:
            await self.session.flush()
            await self.session.delete(r)
        elif r.status == RuleStatus.changing:
            r.status = RuleStatus.published
        await self.session.flush()

    async def save_draft(self, code: str, body: RuleDraftIn) -> dict[str, Any]:
        """R7 保存到草稿：只有在变更单里的规则能改；自动暂存开启时必填参数都要有取值来源。"""
        r = await self.get(code)
        drafts = await self._drafts()
        d = drafts.get(r.id)
        if d is None or (body.cr and body.cr != d.cr_code):
            raise fail("STATE_INVALID", "已发布版本只读，请先发起变更", 409)
        actions = await self._actions()
        cur = await self.draft_def(r, d, actions)
        assert cur is not None
        st = body.auto_stage
        errs: list[tuple[str, str]] = []
        if len(body.thresholds) > len(cur["conds"]):
            errs.append(("thresholds", "阈值个数超过条件个数"))
        for i, t in enumerate(body.thresholds):
            if not t.strip():
                errs.append((f"thresholds[{i}]", "阈值不能为空"))
        at: ActionType | None = None
        if st.action_type_id:
            at = await self.session.scalar(select(ActionType).where(ActionType.code == st.action_type_id))
            if at is None or at.target_kind != r.obj_kind or at.status.value == "草稿":
                errs.append(("autoStage.actionTypeId", "只能选目标对象为规则对象、且不是草稿的动作类型"))
        if st.enabled and not st.action_type_id:
            errs.append(("autoStage.actionTypeId", "开启自动暂存需选择动作类型"))
        if st.confirmer and st.confirmer not in _meta("CONFIRMERS"):
            errs.append(("autoStage.confirmer", "确认人不在可选列表里"))
        for i, m in enumerate(st.mapping):
            if m.from_ and m.from_ not in _meta("MAPPING_SOURCES"):
                errs.append((f"autoStage.mapping[{i}].from", "取值来源不在可选列表里"))
        if errs:
            raise validation(errs)
        if st.enabled and at is not None:
            mapped = {m.param for m in st.mapping if m.from_}
            missing = [p.name for p in at.params if p.required and p.name not in mapped]
            if missing:
                raise fail(
                    "STAGE_MAPPING_MISSING",
                    f"自动暂存动作缺少必填参数的取值来源：{'、'.join(missing)}",
                    422,
                    missing=missing,
                )
        conds = [
            {**c, "value": body.thresholds[i]} if i < len(body.thresholds) else c
            for i, c in enumerate(cur["conds"])
        ]
        auto = {
            "enabled": st.enabled,
            "actionTypeId": st.action_type_id,
            "confirmer": st.confirmer,
            "mapping": [{"param": m.param, "from": m.from_} for m in st.mapping],
        }
        d.patch = {**d.patch, "conds": conds, "autoStage": auto}
        d.by, d.at = self.user.name, now()
        await self.session.flush()
        draft = await self.draft_def(r, d, actions)
        assert draft is not None
        pub = None if r.version is None else self._def(r, actions)
        lv = await CrLink(self.session, self.user).put(
            d.cr_code, self._target(r), rule_sections(pub, draft), body.lock_version
        )
        return {
            "cr": d.cr_code,
            "lockVersion": lv and lv["lockVersion"],
            "crChanges": lv and lv["changes"],
            "draft": draft,
            "message": f"已保存到草稿 {d.cr_code}，发布前不影响线上规则",
        }


# ======================================================================
# 情景推演
# ======================================================================


def no_driver(d: dict[str, Any]) -> bool:
    return (
        d["pricePct"] == 0
        and d["replenishPct"] == 0
        and d["safetyDays"] == 7
        and (d["promo"] == "无" or d["promoDepth"] == 0)
    )


def drivers_dict(d: DriversIn) -> dict[str, Any]:
    return d.model_dump(by_alias=True)


def scope_dict(s: ScopeIn) -> dict[str, Any]:
    return s.model_dump(by_alias=True)


class ScenarioService:
    def __init__(self, session: AsyncSession, user: CurrentUser) -> None:
        self.session = session
        self.user = user

    async def get(self, code: str) -> Scenario:
        s = await self.session.scalar(select(Scenario).where(Scenario.code == code))
        if s is None:
            raise NotFound("方案不存在", details={"id": code})
        return s

    async def _vers(self) -> dict[int, str]:
        return {
            i: v
            for i, v in (await self.session.execute(select(OntologyRelease.id, OntologyRelease.ver))).all()
        }

    async def _result(self, s: Scenario) -> ScenarioResult | None:
        return await self.session.scalar(select(ScenarioResult).where(ScenarioResult.scenario_id == s.id))

    async def _impacts(self, s: Scenario) -> list[dict[str, Any]]:
        rows = (
            await self.session.scalars(
                select(ScenarioImpact).where(ScenarioImpact.scenario_id == s.id).order_by(ScenarioImpact.seq)
            )
        ).all()
        return [
            {
                "sku": x.sku,
                "name": x.name,
                "category": x.category,
                "storeId": x.store_id,
                "storeName": x.store_name,
                "baseSales": x.base_sales,
                "sales": x.sales,
                "stockoutBase": _n(x.stockout_base),
                "stockout": _n(x.stockout),
                "suggest": x.suggest,
            }
            for x in rows
        ]

    @staticmethod
    def result_out(res: ScenarioResult) -> dict[str, Any]:
        return {
            "ranAt": sec(res.ran_at),
            "weeks": res.weeks,
            "baseSales": res.base_sales,
            "sales": res.sales,
            "baseMargin": res.base_margin,
            "margin": res.margin,
            "kpis": res.kpis,
        }

    def lock_reason(self, s: Scenario) -> str:
        if s.owner != self.user.name:
            return f"{s.owner} 的方案，只读（不能修改、运行或转为动作）；如需调整请“复制为新方案”"
        if s.status == ScenarioStatus.running:
            return "推演运行中，完成后可修改"
        if s.staged:
            return f"已转为 {len(s.staged)} 条暂存动作，配置锁定；如需调整请“复制为新方案”"
        return ""

    async def out(self, s: Scenario, vers: dict[int, str], *, full: bool = False) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": s.code,
            "name": s.name,
            "owner": s.owner,
            "createdAt": sec(s.created_on),
            "baseVer": vers.get(s.base_release_id) if s.base_release_id else None,
            "status": s.status.value,
            "scope": s.scope,
            "drivers": s.drivers,
            "staged": list(s.staged or []),
        }
        if s.fail_reason:
            d["failReason"] = s.fail_reason
        if full:
            res = await self._result(s)
            d["result"] = self.result_out(res) if res else None
            d["lockReason"] = self.lock_reason(s)
        return d

    async def list_scenarios(
        self, params: PageParams, *, status: str | None, kw: str | None
    ) -> Page[dict[str, Any]]:
        vers = await self._vers()
        rows = (
            await self.session.scalars(
                select(Scenario).order_by(Scenario.created_on.desc(), Scenario.id.desc())
            )
        ).all()
        out = [
            await self.out(s, vers)
            for s in rows
            if (not status or status == "全部" or s.status.value == status)
            and (not kw or kw in s.name or kw.lower() in s.code.lower())
        ]
        return paginate(out, params)

    async def detail(self, code: str) -> dict[str, Any]:
        return await self.out(await self.get(code), await self._vers(), full=True)

    async def result(self, code: str) -> dict[str, Any]:
        s = await self.get(code)
        if s.status == ScenarioStatus.running:
            return {"status": s.status.value}  # 路由据此回 202，前端继续轮询
        if s.status == ScenarioStatus.failed:
            raise fail("SCENARIO_FAILED", s.fail_reason or "推演失败", 409)
        res = await self._result(s)
        if res is None:
            raise NotFound("方案还没有推演结果", details={"id": code})
        return self.result_out(res)

    async def impacts(self, code: str, params: PageParams) -> Page[dict[str, Any]]:
        return paginate(await self._impacts(await self.get(code)), params)

    async def _next_code(self) -> str:
        codes = (await self.session.scalars(select(Scenario.code))).all()
        n = max((int(c.split("-")[1]) for c in codes if c.startswith("SCN-") and c[4:].isdigit()), default=0)
        return f"SCN-{n + 1:04d}"

    async def create(self, from_: str | None) -> dict[str, Any]:
        src = await self.get(from_) if from_ else None
        cur = await self.session.scalar(
            select(OntologyRelease).order_by(OntologyRelease.published_at.desc()).limit(1)
        )
        from app.modules.dynamic.rules_sim import BASE_DRIVERS

        s = Scenario(
            code=await self._next_code(),
            name=f"{src.name}（副本）" if src else "新方案",
            owner=self.user.name,
            created_on=now(),
            base_release_id=cur.id if cur else None,
            status=ScenarioStatus.draft,
            scope=dict(src.scope) if src else {"area": "华东大区全部", "category": "全部品类", "weeks": 8},
            drivers=dict(src.drivers) if src else dict(BASE_DRIVERS),
            staged=[],
            is_seed=False,
        )
        self.session.add(s)
        await self.session.flush()
        return await self.out(s, await self._vers(), full=True)

    def _own(self, s: Scenario) -> None:
        if s.owner != self.user.name:
            raise fail(
                "SCENARIO_NOT_OWNER",
                f"{s.owner} 的方案，只读（不能修改、运行或转为动作）；如需调整请“复制为新方案”",
                403,
            )

    async def rename(self, code: str, name: str) -> dict[str, Any]:
        s = await self.get(code)
        self._own(s)
        if not name.strip():
            raise validation([("name", "名称不能为空")])
        s.name = name.strip()
        await self.session.flush()
        return await self.out(s, await self._vers(), full=True)

    async def run(self, code: str, body: ScenarioRunIn) -> dict[str, Any]:
        s = await self.get(code)
        self._own(s)
        if s.status == ScenarioStatus.running:
            raise fail("SCENARIO_RUNNING", "推演运行中，完成后可修改", 409)
        if s.staged:
            raise fail(
                "SCENARIO_STAGED_LOCKED",
                f"已转为 {len(s.staged)} 条暂存动作，配置锁定；如需调整请“复制为新方案”",
                409,
            )
        scope, drivers = scope_dict(body.scope), drivers_dict(body.drivers)
        if scope["area"] not in _meta("SC_AREAS"):
            raise validation([("scope.area", "范围不在可选列表里")])
        if scope["category"] not in _meta("SC_CATEGORIES"):
            raise validation([("scope.category", "品类不在可选列表里")])
        if drivers["promo"] not in _meta("PROMO_TYPES"):
            raise validation([("drivers.promo", f"促销方式只能是：{'、'.join(_meta('PROMO_TYPES'))}")])
        if drivers["promo"] == "无":
            drivers["promoDepth"], drivers["promoWeeks"] = 0, 0
        if no_driver(drivers):
            raise fail("SCENARIO_NO_DRIVER", "至少调整一个驱动（价格 / 库存 / 促销）", 422)
        if body.name and body.name.strip():
            s.name = body.name.strip()
        s.scope, s.drivers, s.status, s.fail_reason = scope, drivers, ScenarioStatus.running, None
        old = await self._result(s)
        if old is not None:
            await self.session.delete(old)
        for x in (
            await self.session.scalars(select(ScenarioImpact).where(ScenarioImpact.scenario_id == s.id))
        ).all():
            await self.session.delete(x)
        await self.session.flush()
        return await self.out(s, await self._vers(), full=True)

    async def compute(self, code: str) -> None:
        """后台任务：按 start 时写入的 scope / drivers 计算并落库（运行中 → 已完成 / 失败）。
        EXTENSION POINT (PDE): 真实推演引擎接任务队列时替换这里；
        当前为进程内后台任务，进程重启会留下“运行中” [推断]"""
        s = await self.get(code)
        if s.status != ScenarioStatus.running:
            return
        try:
            # SAVEPOINT：_compute 中途数据库出错只回滚这次计算，会话仍可用来写失败状态
            async with self.session.begin_nested():
                await self._compute(s)
        except Exception as e:  # 推演失败落到方案上，前端轮询 /result 拿到失败原因
            s = await self.get(code)
            s.status, s.fail_reason = ScenarioStatus.failed, f"推演失败：{e}"[:500]
            await self.session.flush()

    async def _compute(self, s: Scenario) -> None:
        scope, drivers = s.scope, s.drivers
        at = now()
        res = simulate(scope, drivers, sec(at))
        self.session.add(
            ScenarioResult(
                scenario_id=s.id,
                ran_at=at,
                scope=scope,
                drivers=drivers,
                weeks=res["weeks"],
                base_sales=res["baseSales"],
                sales=res["sales"],
                base_margin=res["baseMargin"],
                margin=res["margin"],
                kpis=res["kpis"],
                is_seed=False,
            )
        )
        for i, x in enumerate(res["impacts"], 1):
            self.session.add(
                ScenarioImpact(
                    scenario_id=s.id,
                    seq=i,
                    sku=x["sku"],
                    name=x["name"],
                    category=x["category"],
                    store_id=x["storeId"],
                    store_name=x["storeName"],
                    base_sales=x["baseSales"],
                    sales=x["sales"],
                    stockout_base=x["stockoutBase"],
                    stockout=x["stockout"],
                    suggest=x["suggest"],
                    is_seed=False,
                )
            )
        s.status = ScenarioStatus.done
        await self.session.flush()

    async def compare(self, ids: list[str]) -> dict[str, Any]:
        if not ids or len(ids) > 3:
            raise fail("COMPARE_LIMIT", "最多对比 3 个已完成的方案", 422)
        vers = await self._vers()
        out = []
        for code in ids:
            s = await self.get(code)
            if s.status != ScenarioStatus.done:
                raise fail("COMPARE_LIMIT", f"{s.code} 未完成，只能对比已完成的方案", 422)
            out.append(await self.out(s, vers, full=True))
        same = len({(o["scope"]["area"], o["scope"]["category"], o["scope"]["weeks"]) for o in out}) == 1
        return {"items": out, "sameScope": same}

    async def _plan(self, s: Scenario) -> list[dict[str, Any]]:
        res = await self._result(s)
        if res is None:
            return []
        full = {**self.result_out(res), "impacts": await self._impacts(s)}
        return stage_plan(res.scope, res.drivers, full)

    async def plan(self, code: str) -> list[dict[str, Any]]:
        return await self._plan(await self.get(code))

    async def stage(self, code: str, keys: list[str], actions: Any) -> dict[str, Any]:
        """S9：每条建议调 ActionService.stage 生成待确认动作（source=推演）；之后方案锁定。"""
        from app.modules.dynamic.schemas import SourceIn, StageIn

        s = await self.get(code)
        self._own(s)
        if s.status != ScenarioStatus.done:
            raise fail("STATE_INVALID", "推演完成后才能转为暂存动作", 409)
        if s.staged:
            raise fail("SCENARIO_STAGED_LOCKED", f"已转为 {len(s.staged)} 条暂存动作，请到动作中心确认", 409)
        res = await self._result(s)
        assert res is not None
        if res.scope != s.scope or res.drivers != s.drivers:
            raise fail("SCENARIO_CONFIG_DIRTY", "配置改动后需重新运行推演", 409)
        plan = {p["key"]: p for p in await self._plan(s)}
        if not plan:
            raise fail("STATE_INVALID", "没有可转为动作的建议", 409)
        bad = [k for k in keys if k not in plan]
        if not keys or bad:
            raise validation(
                [("keys", "至少勾选一条建议" if not keys else f"不在建议清单里：{'、'.join(bad)}")]
            )
        ids = []
        for k in dict.fromkeys(keys):
            p = plan[k]
            run, _ = await actions.stage(
                StageIn(
                    action=p["action"],
                    source=SourceIn(
                        type="推演", scenario_id=s.code, scenario_name=s.name, confirmer=self.user.name
                    ),
                    obj_kind=p["objKind"],
                    obj_id=p["objId"],
                    obj_label=p["objLabel"],
                    params=p["params"],
                )
            )
            ids.append(run.code)
        s.staged = ids
        await self.session.flush()
        return {"staged": ids, "message": f"已暂存 {len(ids)} 条动作到动作中心（待确认）"}

    def meta(self) -> dict[str, Any]:
        from app.modules.dynamic.rules_sim import BASE_DRIVERS

        return {
            "areas": _meta("SC_AREAS"),
            "categories": _meta("SC_CATEGORIES"),
            "promoTypes": _meta("PROMO_TYPES"),
            "baseDrivers": BASE_DRIVERS,
            "statuses": ["全部", *[x.value for x in ScenarioStatus]],
        }


def _n(x: float) -> float | int:
    return int(x) if float(x).is_integer() else x


# ======================================================================
# 生命周期
# ======================================================================


class LifecycleService:
    def __init__(self, session: AsyncSession, user: CurrentUser) -> None:
        self.session = session
        self.user = user

    async def get(self, object_id: str) -> Lifecycle:
        lc = await self.session.scalar(select(Lifecycle).where(Lifecycle.code == object_id))
        if lc is None:
            raise NotFound("该对象类型没有生命周期", details={"id": object_id})
        return lc

    async def _versions(self, lc: Lifecycle, at: datetime | None = None) -> list[dict[str, Any]]:
        rows = (
            await self.session.scalars(
                select(LifecycleVersion)
                .where(LifecycleVersion.lifecycle_id == lc.id)
                .order_by(LifecycleVersion.version.desc())
            )
        ).all()
        vers = {
            i: v
            for i, v in (await self.session.execute(select(OntologyRelease.id, OntologyRelease.ver))).all()
        }
        rows = [v for v in rows if at is None or v.at <= at]
        eff = rows[0].version if at is not None and rows else lc.version
        out = []
        for v in rows:
            d: dict[str, Any] = {
                "ver": v.version,
                "status": "生效中" if v.version == eff else "已替代",
                "by": v.by,
                "at": _fmt_dt(v.at),
                "change": v.change,
            }
            if v.release_id:
                d["release"] = vers.get(v.release_id)
            if v.undo:
                d["undo"] = v.undo
            out.append(d)
        return out

    async def detail(
        self, object_id: str, *, view: str | None, cr: str | None, release: OntologyRelease | None
    ) -> dict[str, Any]:
        """L1。?ver= 历史版本 = 该版发布时生效的状态机（lifecycleAsOf，取发布前最近的生命周期版本快照）。"""
        lc = await self.get(object_id)
        if release is not None:
            at = release.published_at
            vs = await self._versions(lc, at)
            if vs and vs[0]["ver"] != lc.version:
                v = await version_row(self.session, lc, vs[0]["ver"])
                assert v is not None and v.snapshot is not None
                head = {**lifecycle_head(lc), "version": v.version, "updatedAt": _fmt_dt(v.at)}
                return {**head, **v.snapshot, "versions": vs, "draft": None, "readonly": True}
            return {
                **(await lifecycle_def(self.session, lc)),
                "versions": vs,
                "draft": None,
                "readonly": True,
            }
        out = {**(await lifecycle_def(self.session, lc)), "versions": await self._versions(lc)}
        draft = None
        if view == "draft":
            q = select(LifecycleDraft).where(LifecycleDraft.lifecycle_id == lc.id)
            if cr:
                q = q.where(LifecycleDraft.cr_code == cr)
            d = await self.session.scalar(q.order_by(LifecycleDraft.id.desc()).limit(1))
            link = CrLink(self.session, self.user)
            if d and await link.exists(d.cr_code) and not await link.registered(d.cr_code, "object", lc.code):
                d = None  # 变更单里已放弃本对象（static DELETE /targets），残留草稿行不再算数
            draft = {"cr": d.cr_code, "edits": d.edits} if d else {"cr": cr, "edits": empty_edits()}
        out["draft"] = draft
        return out

    async def changes(self, object_id: str, params: PageParams, *, illegal: bool) -> Page[dict[str, Any]]:
        return paginate(await self._changes(await self.get(object_id), illegal=illegal), params)

    async def _changes(self, lc: Lifecycle, *, illegal: bool) -> list[dict[str, Any]]:
        q = select(LifecycleChange).where(LifecycleChange.lifecycle_id == lc.id)
        if illegal:
            q = q.where(LifecycleChange.transition_id.is_(None))
        rows = (
            await self.session.scalars(q.order_by(LifecycleChange.at.desc(), LifecycleChange.code.desc()))
        ).all()
        trs = {
            i: c
            for i, c in (
                await self.session.execute(
                    select(LifecycleTransition.id, LifecycleTransition.tr_code).where(
                        LifecycleTransition.id.in_({r.transition_id for r in rows if r.transition_id})
                    )
                )
            ).all()
        }
        alerts = {
            i: c
            for i, c in (
                await self.session.execute(
                    select(Alert.id, Alert.code).where(Alert.id.in_({r.alert_id for r in rows if r.alert_id}))
                )
            ).all()
        }
        return [
            {
                "id": r.code,
                "at": sec(r.at),
                "objId": r.obj_id,
                "objLabel": r.obj_label,
                "from": r.from_state,
                "to": r.to_state,
                "trId": trs.get(r.transition_id) if r.transition_id else None,
                "by": r.by,
                "alertId": alerts.get(r.alert_id) if r.alert_id else None,
            }
            for r in rows
        ]

    async def illegal(self, object_id: str, since: str | None) -> list[dict[str, Any]]:
        """lifecycle.ts illegalOf：近 7 天非法迁移按「原 → 新」汇总
        （= SM-ORD 命中 = transition 为空的变化）。"""
        lc = await self.get(object_id)
        if not lc.rule_code:
            return []
        cut = SINCE_7D if since in (None, "", "7d") else since
        groups: dict[str, dict[str, Any]] = {}
        for c in await self._changes(lc, illegal=True):
            if c["at"] < cut:
                continue
            g = groups.setdefault(
                f"{c['from']} → {c['to']}",
                {"from": c["from"], "to": c["to"], "count": 0, "alertId": None, "lastAt": c["at"]},
            )
            g["count"] += 1
            if not g["alertId"] and c["alertId"]:
                g["alertId"] = c["alertId"]
            if c["at"] > g["lastAt"]:
                g["lastAt"] = c["at"]
        return sorted(groups.values(), key=lambda g: -g["count"])

    async def save_draft(self, object_id: str, body: LifecycleDraftIn) -> dict[str, Any]:
        """L3–L6：整份草稿改动校验后保存（键 = 变更单号:对象类型）。"""
        lc = await self.get(object_id)
        if not body.cr:
            raise fail("STATE_INVALID", "已发布版本只读，请先发起变更", 409)
        cur = await lifecycle_def(self.session, lc)
        errs = validate_lc_edits(cur, body)
        if errs:
            raise validation(errs)
        edits = {
            "addStates": body.add_states,
            "addTransitions": body.add_transitions,
            "editTransitions": body.edit_transitions,
            "removeTransitions": body.remove_transitions,
        }
        d = await self.session.scalar(
            select(LifecycleDraft).where(
                LifecycleDraft.lifecycle_id == lc.id, LifecycleDraft.cr_code == body.cr
            )
        )
        if d is None:
            d = LifecycleDraft(lifecycle_id=lc.id, cr_code=body.cr, edits=edits, is_seed=False)
            self.session.add(d)
        else:
            d.edits = edits
        await self.session.flush()
        target = TargetIn(kind="object", id=lc.code, name=lc.object_name)
        lv = await CrLink(self.session, self.user).put(
            body.cr, target, lifecycle_sections(cur, edits), body.lock_version
        )
        return {
            "cr": body.cr,
            "edits": edits,
            "lockVersion": lv and lv["lockVersion"],
            "crChanges": lv and lv["changes"],
        }


def empty_edits() -> dict[str, Any]:
    return {"addStates": [], "addTransitions": [], "editTransitions": {}, "removeTransitions": []}


def validate_lc_edits(cur: dict[str, Any], body: LifecycleDraftIn) -> list[tuple[str, str]]:
    errs: list[tuple[str, str]] = []
    names = {s["name"] for s in cur["states"]}
    keys = {s["key"] for s in cur["states"]}
    for i, s in enumerate(body.add_states):
        n = str(s.get("name") or "").strip()
        if not n:
            errs.append((f"addStates[{i}].name", "状态名称必填"))
        elif n in names:
            errs.append((f"addStates[{i}].name", f"状态“{n}”已存在"))
        names.add(n)
        keys.add(str(s.get("key") or n))
    tr_ids = {t["id"] for t in cur["transitions"]}
    pairs = {(t["from"], t["to"]) for t in cur["transitions"] if t["id"] not in body.remove_transitions}
    for i, t in enumerate(body.add_transitions):
        f, to, by = t.get("from"), t.get("to"), str(t.get("by") or "").strip()
        if not f or f not in keys:
            errs.append((f"addTransitions[{i}].from", "原状态必填且须存在"))
        if not to or to not in keys:
            errs.append((f"addTransitions[{i}].to", "新状态必填且须存在"))
        if not by:
            errs.append((f"addTransitions[{i}].by", "触发来源必填"))
        if (f, to) in pairs:
            errs.append((f"addTransitions[{i}]", f"迁移“{f} → {to}”已存在"))
        pairs.add((f, to))
    for k, e in body.edit_transitions.items():
        if k not in tr_ids:
            errs.append((f"editTransitions.{k}", "迁移不存在"))
        elif "by" in e and not str(e["by"] or "").strip():
            errs.append((f"editTransitions.{k}.by", "触发来源必填"))
    for k in body.remove_transitions:
        if k not in tr_ids:
            errs.append((f"removeTransitions.{k}", "迁移不存在"))
    return errs


__all__ = ["LifecycleService", "RuleService", "ScenarioService"]
