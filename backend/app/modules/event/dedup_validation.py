"""Validate only new or explicitly changed event deduplication windows."""

from sqlalchemy import select

from app.core.errors import AppError
from app.modules.event.models import EventType
from app.modules.static.models import CrItem
from app.modules.static.writeback import writeback_error


def validate_window(value):
    from app.modules.event.ingress import dedup_window

    try:
        if not isinstance(value, str):
            raise ValueError
        return dedup_window(value)
    except (AppError, ValueError):
        raise writeback_error("去重窗口须为正整数秒、分钟、小时、天或永久，例如 24 小时", field="dedupWindow") from None


async def validate_draft_window(session, code, cr_id, *, section=None, rows=(), complete=False):
    current = await session.scalar(select(EventType).where(EventType.code == code))
    saved = list(await session.scalars(select(CrItem).where(
        CrItem.cr_id == cr_id, CrItem.target_kind == "event", CrItem.target_id == code,
        CrItem.section == "def", CrItem.item_key == "dedupWindow")))
    changes = saved if section != "def" else []
    proposed = [row for row in rows if row.key == "dedupWindow"] if section == "def" else []
    if not changes and not proposed and (current is not None or (not complete and section != "def")):
        return
    value = current.dedup_window if current else None
    for item in changes:
        value = None if item.op == "delete" else item.after
    for row in proposed:
        value = None if row.op == "delete" else row.after
    validate_window(value)
