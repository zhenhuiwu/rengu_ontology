"""W7 流式回答（SSE，qa-api §3.1）。

- 事件从回答快照拆出来（meta / basis / clarify / text / table / citations / suggest /
  denied / empty / done），
  每个事件先写 `qa_turn_event(turn_id, seq)` 再推送；`GET /turns/{tid}/stream` 按 `Last-Event-ID` 从表里续传 /
  回放（旧轮次没有事件就按快照现拆，不落库）。
- `status` 事件只在实时流里推（链路阶段），不落库、没有 `id`，续传不回放。
- 编码用 sse-starlette 的思路自己写（几行，不加依赖）：`id:` / `event:` / `data:` + 空行。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.qa.models import QaTurn, QaTurnEvent

MEDIA_TYPE = "text/event-stream"
HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}  # 反向代理别缓冲（qa-api §12 信创备注）


def sse(event: str, data: Any, eid: int | None = None) -> str:
    body = json.dumps(data, ensure_ascii=False, default=str)
    head = f"id: {eid}\n" if eid is not None else ""
    return (
        head + f"event: {event}\n" + "".join(f"data: {line}\n" for line in body.splitlines() or [""]) + "\n"
    )


def turn_events(turn: QaTurn, turn_payload: dict[str, Any]) -> list[tuple[str, Any]]:
    """快照 → 有序事件列表；最后一条 `done` 带完整轮次（同 W3 形状），前端可以直接追加。"""
    snap = turn.snapshot or {}
    kind = turn.kind.value
    ev: list[tuple[str, Any]] = []
    ans = snap.get("answer") if isinstance(snap.get("answer"), dict) else None
    if kind == "answer" and ans is not None:
        ev.append(("meta", {"answeredVersion": turn.answered_ver, "dataAsOf": ans.get("dataAsOf")}))
        if ans.get("basis"):
            ev.append(("basis", ans["basis"]))
        if ans.get("clarify") or ans.get("card") == "clarify":
            ev.append(
                ("clarify", ans.get("clarify") or {k: ans.get(k) for k in ("term", "question", "options")})
            )
        if ans.get("text"):
            ev.append(("text", {"full": ans["text"]}))
        if ans.get("table"):
            ev.append(("table", ans["table"]))
        cites = {
            k: ans[k] for k in ("alerts", "dynamicSummary", "dynamicSummaries", "metricQuery") if ans.get(k)
        }
        if cites:  # 只读引用：相关告警 / 点名对象的动态摘要 / 指标层查询（不含 SQL）
            ev.append(("citations", cites))
        if ans.get("action"):
            ev.append(("suggest", {"followUps": [], "actions": [ans["action"]]}))
    elif kind == "denied":
        ev.append(("denied", {k: v for k, v in snap.items() if k != "answer"}))
    elif kind == "empty":
        ev.append(("empty", snap))
    ev.append(
        ("done", {"turnId": turn.code, "kind": kind, "elapsedMs": turn.elapsed_ms, "turn": turn_payload})
    )
    return ev


async def write_events(session: AsyncSession, turn: QaTurn, events: list[tuple[str, Any]]) -> None:
    for i, (name, payload) in enumerate(events, start=1):
        session.add(QaTurnEvent(turn_id=turn.id, seq=i, event_name=name, payload={"v": payload}))
    await session.flush()


async def stored_events(session: AsyncSession, turn: QaTurn, after: int = 0) -> list[tuple[int, str, Any]]:
    rows = (
        await session.scalars(
            select(QaTurnEvent)
            .where(QaTurnEvent.turn_id == turn.id, QaTurnEvent.seq > after)
            .order_by(QaTurnEvent.seq)
        )
    ).all()
    return [(e.seq, e.event_name, (e.payload or {}).get("v")) for e in rows]


def last_event_id(raw: str | None) -> int:
    try:
        return max(0, int((raw or "0").strip()))
    except ValueError:
        return 0
