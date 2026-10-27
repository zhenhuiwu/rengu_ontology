"""告警产生后的自动决策评估（只给建议，尽力而为）。

不在告警创建事务里做：定时规则 / 事件规则建告警的事务提交之后，由动态调度 worker
（python -m app.modules.dynamic.worker，每分钟 rule.sweep）收尾调用 decide_pending：
找近 24 小时内、规则运行产生（非 seed）、仍未关闭、还没有任何决策记录的告警，逐条在独立事务里
用告警的创建身份（规则运行身份）跑 DecisionService.evaluate（dryRun=true，caller kind = system），
结果写 decision_log（alert_code 关联告警）。单条失败只记日志，并写一条带 error 的决策记录避免反复重试；
任何异常都不会影响告警本身。没有启用 worker 时，前端「AI 处置建议」面板可由人手动生成。
"""

import logging
import secrets
from datetime import timedelta
from typing import Any

from sqlalchemy import exists, select

from app.common.auth import resolve_user
from app.core import clock
from app.jobs.scope import scoped_sessions
from app.modules.dynamic.models import Alert, AlertStatus, DecisionLog

logger = logging.getLogger(__name__)
WINDOW = timedelta(hours=24)
BATCH = 20


async def run_as(session, alert: Alert) -> str | None:
    """评估身份：告警创建身份（规则运行身份）→ 规则触发配置的 runAsUserId → 规则发布人。"""
    if alert.created_by:
        return alert.created_by
    from app.modules.dynamic.rules_models import Rule

    rule = await session.scalar(select(Rule).where(Rule.code == alert.rule_code))
    if rule is None:
        return None
    return (rule.trigger or {}).get("runAsUserId") or rule.published_by


async def decide(session, alert: Alert) -> dict[str, Any]:
    from app.modules.dynamic.catalog import _load_catalog
    from app.modules.dynamic.decisions import DecisionEvaluateIn, DecisionService
    from app.modules.dynamic.ports import Ports, get_checker, get_emitter, get_scheduler, get_submitter

    user = await resolve_user(session, await run_as(session, alert))
    catalog = await _load_catalog(session, "full", user=user)
    ports = Ports(
        get_submitter(session, user), get_emitter(session), get_scheduler(session), get_checker(session, user)
    )
    svc = DecisionService(
        session, user, catalog, ports, None, system={"trigger": "rule", "ruleId": alert.rule_code}
    )
    return await svc.evaluate(DecisionEvaluateIn(alert_id=alert.code, dry_run=True, limit=5))


async def _record_failure(sessions, alert_id: int, error: str) -> None:
    async with sessions() as session, session.begin():
        alert = await session.get(Alert, alert_id)
        if alert is None:
            return
        now = clock.now()
        actor = await run_as(session, alert) or "system"
        session.add(
            DecisionLog(
                code=f"DEC-{now:%Y%m%d%H%M%S}-{secrets.token_hex(3)}",
                caller_kind="system",
                caller_id=actor,
                caller_name=f"规则 {alert.rule_code} 自动评估",
                obj_kind=alert.obj_kind,
                obj_id=alert.obj_id,
                alert_code=alert.code,
                input={
                    "caller": {"kind": "system", "system": {"trigger": "rule", "ruleId": alert.rule_code}}
                },
                output={"suggestOnly": True, "recommendations": [], "notes": [], "error": error[:500]},
                created_by=actor,
                created_at=now,
            )
        )


async def decide_pending(ontology_id: int, *, limit: int = BATCH) -> int:
    sessions = scoped_sessions(ontology_id)
    async with sessions() as session, session.begin():
        ids = list(
            await session.scalars(
                select(Alert.id)
                .where(
                    Alert.is_seed.is_(False),
                    Alert.status.in_((AlertStatus.open, AlertStatus.processing)),
                    Alert.triggered_at > clock.now() - WINDOW,
                    ~exists().where(DecisionLog.alert_code == Alert.code),
                )
                .order_by(Alert.triggered_at)
                .limit(limit)
            )
        )
    done = 0
    for alert_id in ids:
        try:
            async with sessions() as session, session.begin():
                alert = await session.get(Alert, alert_id)
                if alert is not None:
                    await decide(session, alert)
                    done += 1
        except Exception as exc:  # 尽力而为：失败不影响告警
            logger.exception("Auto decision failed for alert id %s", alert_id)
            try:
                await _record_failure(sessions, alert_id, getattr(exc, "message", None) or repr(exc))
            except Exception:
                logger.exception("Auto decision failure record failed for alert id %s", alert_id)
    return done
