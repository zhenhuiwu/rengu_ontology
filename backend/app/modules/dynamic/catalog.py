"""动作中心的请求级真实定义目录，不读取种子文件。"""

import re
from collections import Counter
from typing import Annotated, Any, Protocol

from fastapi import Path
from sqlalchemy import select
from sqlalchemy.orm import joinedload, raiseload

from app.common.auth import CurrentUserDep
from app.db.session import SessionDep


class RefCatalog(Protocol):
    def rule(self, code: str) -> dict[str, Any] | None: ...
    def action_type(self, code: str) -> dict[str, Any] | None: ...
    def action_type_by_name(self, name: str) -> dict[str, Any] | None: ...
    def action_meta(self, name: str) -> dict[str, Any] | None: ...
    def person(self, text: str | None) -> dict[str, str] | None: ...
    def ext_system(self, code: str | None) -> dict[str, Any] | None: ...
    def meta(self) -> dict[str, Any]: ...


class DatabaseRefCatalog:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    @property
    def _rules(self) -> dict[str, dict[str, Any]]:
        return self._cached("dynamic/RULES.json")

    def _cached(self, rel: str) -> Any:
        return self.data[rel]

    def rule(self, code: str) -> dict[str, Any] | None:
        r = self._rules.get(code)
        if r is None:
            return None
        return {**r, "confirmer": self._cached("dynamic/RULE_CONFIRMER.json").get(code)}

    def action_type(self, code: str) -> dict[str, Any] | None:
        return next((t for t in self._cached("action/ACTION_TYPES.json") if t["id"] == code), None)

    def action_type_by_name(self, name: str) -> dict[str, Any] | None:
        matches = [row for row in self._cached("action/ACTION_TYPES.json") if row["name"] == name]
        return matches[0] if len(matches) == 1 else None

    def action_meta(self, name: str) -> dict[str, Any] | None:
        by_id = self.data.get("actionMetaById", {})
        if name in by_id:
            return by_id[name]
        return self._cached("dynamic/ACTION_META.json").get(name)

    def person(self, text: str | None) -> dict[str, str] | None:
        """只匹配完整编号/姓名token；同名或一段文字含多个人时不选人。"""
        if not isinstance(text, str) or not text.strip():
            return None
        exact = [p for p in self.data.get("people", []) if p["id"] == text.strip()]
        if exact:
            return exact[0]
        tokens = set(re.findall(r"[A-Za-z0-9_]+|[\u3400-\u9fff]+", text))
        people = [p for p in self.data.get("people", []) if p["id"] in tokens or p["name"] in tokens]
        return people[0] if len(people) == 1 else None

    def ext_system(self, code: str | None) -> dict[str, Any] | None:
        if not code:
            return None
        return next((s for s in self._cached("system/EXT_SYSTEMS.json") if s["id"] == code), None)

    def meta(self) -> dict[str, Any]:
        """GET /actions/meta：动作中心筛选项与「新建动作」需要的元数据（前端同名常量）。"""
        return {
            "actionNames": self._cached("dynamic/ACTION_NAMES.json"),
            "actionSystems": self._cached("dynamic/ACTION_SYSTEMS.json"),
            "actionMeta": {**self._cached("dynamic/ACTION_META.json"), **self.data.get("actionMetaById", {})},
            "objKindLabel": self._cached("dynamic/OBJ_KIND_LABEL.json"),
        }

    # 页面文字里用到的派生值
    def system_name(self, code: str | None) -> str | None:
        s = self.ext_system(code)
        return s["name"] if s else None

    def waiting_text(self, code: str | None) -> str:
        """前端 waitingText(sys)：超过 N 秒未返回将标记为结果未知。"""
        return f"超过 {self.timeout_sec(code)} 秒未返回将标记为结果未知"

    def timeout_sec(self, code: str | None) -> str:
        """前端 timeoutSec(sys) = (timeoutMs ?? 5000) / 1000，按 JS 数字格式输出（5、2.5）。"""
        s = self.ext_system(code)
        ms = s.get("timeoutMs") if s else None
        v = (ms if ms is not None else 5000) / 1000
        return str(int(v)) if v == int(v) else str(v)


async def get_catalog(session: SessionDep, user: CurrentUserDep) -> DatabaseRefCatalog:
    return await _load_catalog(session, "full", user=user)


async def get_list_catalog(session: SessionDep) -> DatabaseRefCatalog:
    """运行列表仅需姓名/系统/对象标签，不装载动作子定义、规则和全库命中统计。"""
    return await _load_catalog(session, "list")


async def get_meta_catalog(session: SessionDep) -> DatabaseRefCatalog:
    return await _load_catalog(session, "meta")


async def get_alert_catalog(
    code: Annotated[str, Path(pattern=r"^[^\x00]+$")], session: SessionDep, user: CurrentUserDep
) -> DatabaseRefCatalog:
    from app.modules.dynamic.models import Alert

    rule_code = await session.scalar(select(Alert.rule_code).where(Alert.code == code))
    return await _load_catalog(session, "alert", rule_code=rule_code, user=user)


async def _load_catalog(session, mode: str, *, rule_code: str | None = None, user=None) -> DatabaseRefCatalog:
    from app.modules.action.models import ActionType, TypeStatus
    from app.modules.action.sections import action_def
    from app.modules.admin.models import SysUser, UserStatus
    from app.modules.dynamic.repo import recent_rule_hits
    from app.modules.dynamic.rules_models import Rule
    from app.modules.dynamic.sections import rule_def
    from app.modules.integration.models import ExtSystem
    from app.modules.static.models import ObjectStatus, ObjectType

    types = []
    if mode != "list":
        statement = (
            select(ActionType)
            .where(ActionType.retired_release_id.is_(None), ActionType.status != TypeStatus.draft)
            .order_by(ActionType.code)
        )
        if mode == "alert":
            statement = statement.where(
                ActionType.id.in_(select(Rule.action_type_id).where(Rule.code == rule_code))
            )
        if mode != "full":
            statement = statement.options(raiseload("*"), joinedload(ActionType.system))
        types = list(await session.scalars(statement))
    actions = [action_def(row) if mode == "full" else {"id": row.code, "name": row.name} for row in types]
    by_id = {row.id: row for row in types}
    rules = {}
    if mode in ("full", "alert"):
        statement = select(Rule).where(Rule.version.is_not(None)).order_by(Rule.code)
        if mode == "alert":
            statement = statement.where(Rule.code == rule_code)
        rule_rows = list(await session.scalars(statement))
        weekly = (
            await recent_rule_hits(session, rule_ids={row.id for row in rule_rows}, user=user) if user else {}
        )
        for row in rule_rows:
            action = by_id.get(row.action_type_id)
            rules[row.code] = {
                **rule_def(row, action.code if action else None),
                "action": action.name if action else None,
                "confirmer": row.confirmer,
                "weekly": weekly.get(row.id, 0),
            }
    people = [
        {"id": code, "name": name}
        for code, name in await session.execute(
            select(SysUser.code, SysUser.name)
            .where(SysUser.status == UserStatus.active)
            .order_by(SysUser.code)
        )
    ]
    catalog = DatabaseRefCatalog({"people": people})
    counts = Counter(row.name for row in types)
    meta, by_code = {}, {}
    for row in types:
        approval = row.approval.strip()
        exempt = approval == "免审"
        person = catalog.person(approval) if not exempt else None
        item = {
            "sysId": row.system.code if row.system else None,
            "kind": row.target_kind,
            "version": row.version,
            "approver": person["name"] if person else None,
            "approverId": person["id"] if person else None,
            "approval": row.approval,
            "approvalRequired": not exempt,
            "approvalConfigured": exempt or person is not None,
            "call": row.endpoint,
        }
        by_code[row.code] = item
        if counts[row.name] == 1:
            meta[row.name] = item
    systems = [
        {
            "id": row.code,
            "name": row.name,
            "timeoutMs": row.timeout_ms,
            "maxRetry": row.max_retry,
            "retry": row.retry_text,
            "idempotent": row.idempotent,
            "env": row.env,
            "status": row.status.value,
        }
        for row in await session.scalars(select(ExtSystem).order_by(ExtSystem.code))
    ]
    objects = await session.execute(
        select(ObjectType.code, ObjectType.display_name).where(ObjectType.status == ObjectStatus.published)
    )
    return DatabaseRefCatalog(
        {
            "dynamic/RULES.json": rules,
            "dynamic/RULE_CONFIRMER.json": {code: row["confirmer"] for code, row in rules.items()},
            "action/ACTION_TYPES.json": actions,
            "dynamic/ACTION_META.json": meta,
            "actionMetaById": by_code,
            "people": people,
            "system/EXT_SYSTEMS.json": systems,
            "dynamic/ACTION_NAMES.json": [row["name"] for row in actions if counts[row["name"]] == 1],
            "dynamic/ACTION_SYSTEMS.json": [{"id": row["id"], "name": row["name"]} for row in systems],
            "dynamic/OBJ_KIND_LABEL.json": dict(objects.all()),
        }
    )
