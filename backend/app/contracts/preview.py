"""契约：预览会话（EXTENSION POINT (PDE)）。

规则：**每个用户同一时间只有一个预览会话**。开始新预览会替换旧的（不是报错），结束后恢复本人身份。
`?previewAs=` 参数仍由 app/common/context.py 解析；PDE 实现时校验它和当前会话一致。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class PreviewSession:
    user_id: str
    preview_as: str  # 被预览的用户
    started_at: datetime


@runtime_checkable
class PreviewSessions(Protocol):
    async def start(self, user_id: str, preview_as: str) -> PreviewSession:
        """开始预览；该用户已有会话时替换（一人一会话）。"""
        ...

    async def current(self, user_id: str) -> PreviewSession | None: ...

    async def end(self, user_id: str) -> None: ...


class InMemoryPreviewSessions:
    """dev/test 用的参考实现（进程内）。prod 由 PDE 换成落库/缓存实现。"""

    def __init__(self) -> None:
        self._by_user: dict[str, PreviewSession] = {}

    async def start(self, user_id: str, preview_as: str) -> PreviewSession:
        from app.core import clock

        sess = PreviewSession(user_id, preview_as, clock.now())
        self._by_user[user_id] = sess
        return sess

    async def current(self, user_id: str) -> PreviewSession | None:
        return self._by_user.get(user_id)

    async def end(self, user_id: str) -> None:
        self._by_user.pop(user_id, None)
