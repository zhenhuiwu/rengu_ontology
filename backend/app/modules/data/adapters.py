"""测试连接适配器。dev/test 一律用 mock（不真连外部库/外部系统），prod 由负责人接真实实现。

data（数据连接）和 integration（外部系统）的“测试连接”接口都通过 get_connection_tester() 取实现。
"""

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from app.core.config import get_settings


@dataclass(frozen=True)
class ConnectionTestResult:
    ok: bool
    latency_ms: int
    message: str


@runtime_checkable
class ConnectionTester(Protocol):
    async def test(self, kind: str, config: dict[str, Any]) -> ConnectionTestResult: ...


class MockConnectionTester:
    """固定返回成功；config 里带 `"mockFail": true` 时返回失败，方便前端演示失败态。"""

    async def test(self, kind: str, config: dict[str, Any]) -> ConnectionTestResult:
        if config.get("mockFail"):
            return ConnectionTestResult(False, 3000, "连接超时（mock）")
        return ConnectionTestResult(True, 42, "连接成功（mock）")


def get_connection_tester() -> ConnectionTester:
    if get_settings().env != "prod":
        return MockConnectionTester()
    # EXTENSION POINT（data / integration 负责人）：按 kind 返回真实适配器
    raise NotImplementedError("prod 测试连接适配器尚未接入")
