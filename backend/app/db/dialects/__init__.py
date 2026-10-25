"""数据库方言适配层（接缝）。

业务代码里凡是用到“某个库特有”的能力（UPSERT、事务级锁、JSON 运算……），一律通过
`get_adapter(session)` 拿到适配器再调用，不要在模块里写 PG 专有 SQL。

现在只实现 PostgreSQL。以后加达梦 / 金仓：
1. 新建 dm.py / kingbase.py，实现 DialectAdapter 协议；
2. 在 _ADAPTERS 里按 SQLAlchemy 方言名注册；
3. 跑一遍 tests（测试会对所有已注册的适配器做契约测试，待补）。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.dialects.base import DialectAdapter
from app.db.dialects.postgres import PostgresAdapter

_ADAPTERS: dict[str, DialectAdapter] = {"postgresql": PostgresAdapter()}


def get_adapter(session: AsyncSession) -> DialectAdapter:
    name = session.get_bind().dialect.name
    try:
        return _ADAPTERS[name]
    except KeyError as e:  # pragma: no cover
        raise NotImplementedError(f"数据库方言 {name} 还没有适配器，见 app/db/dialects/__init__.py") from e


__all__ = ["DialectAdapter", "get_adapter"]
