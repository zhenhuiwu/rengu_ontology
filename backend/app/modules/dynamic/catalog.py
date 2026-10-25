"""[桩] 规则 / 动作类型 / 外部系统的只读目录。

STUB — 这些表还不在骨架里：
- rule（本模块下一迭代）、action_type、ext_system（Palantir专家模块）；
在它们合并之前，用 export-mock 导出的 JSON（seeds/json/...）提供同样的信息：
  dynamic/RULES.json、dynamic/RULE_CONFIRMER.json、dynamic/ACTION_META.json、dynamic/ACTION_NAMES.json、
  dynamic/ACTION_SYSTEMS.json、dynamic/OBJ_KIND_LABEL.json、action/ACTION_TYPES.json、system/EXT_SYSTEMS.json。
表合并后换成查库的实现（同一个 Protocol），调用方不用改。
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from app.core.config import get_settings


class RefCatalog(Protocol):
    def rule(self, code: str) -> dict[str, Any] | None: ...
    def action_type(self, code: str) -> dict[str, Any] | None: ...
    def action_type_by_name(self, name: str) -> dict[str, Any] | None: ...
    def action_meta(self, name: str) -> dict[str, Any] | None: ...
    def ext_system(self, code: str | None) -> dict[str, Any] | None: ...
    def meta(self) -> dict[str, Any]: ...


class JsonRefCatalog:
    """STUB：读 seeds/json 里的前端 mock 导出。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._cache: dict[str, Any] = {}

    def _load(self, rel: str) -> Any:
        return json.loads((self.root / rel).read_text(encoding="utf-8"))

    @property
    def _rules(self) -> dict[str, dict[str, Any]]:
        return self._cached("dynamic/RULES.json")

    def _cached(self, rel: str) -> Any:
        if rel not in self._cache:
            self._cache[rel] = self._load(rel)
        return self._cache[rel]

    def rule(self, code: str) -> dict[str, Any] | None:
        r = self._rules.get(code)
        if r is None:
            return None
        return {**r, "confirmer": self._cached("dynamic/RULE_CONFIRMER.json").get(code)}

    def action_type(self, code: str) -> dict[str, Any] | None:
        return next((t for t in self._cached("action/ACTION_TYPES.json") if t["id"] == code), None)

    def action_type_by_name(self, name: str) -> dict[str, Any] | None:
        return next((t for t in self._cached("action/ACTION_TYPES.json") if t["name"] == name), None)

    def action_meta(self, name: str) -> dict[str, Any] | None:
        return self._cached("dynamic/ACTION_META.json").get(name)

    def ext_system(self, code: str | None) -> dict[str, Any] | None:
        if not code:
            return None
        return next((s for s in self._cached("system/EXT_SYSTEMS.json") if s["id"] == code), None)

    def meta(self) -> dict[str, Any]:
        """GET /actions/meta：动作中心筛选项与「新建动作」需要的元数据（前端同名常量）。"""
        return {
            "actionNames": self._cached("dynamic/ACTION_NAMES.json"),
            "actionSystems": self._cached("dynamic/ACTION_SYSTEMS.json"),
            "actionMeta": self._cached("dynamic/ACTION_META.json"),
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


@lru_cache
def get_catalog() -> JsonRefCatalog:
    return JsonRefCatalog(get_settings().seeds_json_dir)
