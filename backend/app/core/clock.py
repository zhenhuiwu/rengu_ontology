"""统一时钟。**所有“现在”都从这里取**，不要直接调用 datetime.now() / date.today() / SQL now()。

- 生产（RENGU_ENV=prod）：真实时间；
- dev / test / seed：固定在 RENGU_FIXED_NOW（默认 2026-10-25T15:42:00+08:00，和前端 mock 的“现在”一致），
  这样“今天 / 近 7 天 / 昨天 21:17 / 超时 30 分钟”等逻辑和页面上看到的完全一样。
- 后台任务（procrastinate 等）里的到期、对账间隔、“今天”判断同样用 now() / today()。
- 测试里临时改时间：`with clock.frozen(datetime(...)):`。
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import get_settings

_override: ContextVar[datetime | None] = ContextVar("clock_override", default=None)


def now() -> datetime:
    """带时区的当前时间（UTC）。"""
    o = _override.get()
    if o is not None:
        return o.astimezone(UTC)
    fixed = get_settings().effective_fixed_now
    return fixed.astimezone(UTC) if fixed else datetime.now(UTC)


def local_now() -> datetime:
    """业务时区（北京时间）的当前时间。"""
    return now().astimezone(ZoneInfo(get_settings().display_tz))


def today() -> date:
    """业务时区的“今天”。"""
    return local_now().date()


def last_days(n: int) -> tuple[date, date]:
    """“近 n 天”（含今天）：返回 [起始日, 今天]。"""
    t = today()
    return t - timedelta(days=n - 1), t


@contextmanager
def frozen(at: datetime) -> Iterator[None]:
    token = _override.set(at)
    try:
        yield
    finally:
        _override.reset(token)
