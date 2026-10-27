"""汇总所有 ORM 模型，供 Alembic autogenerate 和测试建表使用。新模块的 models 在这里 import。"""

from app.common import outbox as _outbox  # noqa: F401
from app.db.base import Base
from app.modules.action import models as _action  # noqa: F401
from app.modules.admin import models as _admin  # noqa: F401
from app.modules.biz import models as _biz  # noqa: F401
from app.modules.data import models as _data  # noqa: F401
from app.modules.data import quality_models as _quality  # noqa: F401
from app.modules.dynamic import models as _dynamic  # noqa: F401
from app.modules.dynamic import rules_models as _dynamic_rules  # noqa: F401
from app.modules.event import models as _event  # noqa: F401
from app.modules.functions import models as _functions  # noqa: F401
from app.modules.integration import models as _integration  # noqa: F401
from app.modules.notifications import models as _notifications  # noqa: F401
from app.modules.ontology import models as _ontology  # noqa: F401
from app.modules.qa import models as _qa  # noqa: F401
from app.modules.static import models as _static  # noqa: F401
from app.modules.versioning import models as _versioning  # noqa: F401

__all__ = ["Base"]
