"""汇总所有 ORM 模型，供 Alembic autogenerate 和测试建表使用。新模块的 models 在这里 import。"""

from app.db.base import Base
from app.modules.versioning import models as _versioning  # noqa: F401

__all__ = ["Base"]
