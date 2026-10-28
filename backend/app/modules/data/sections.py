"""Quality definition snapshots only; immutable runs are never rolled back."""

from sqlalchemy import delete, select

from app.core import clock
from app.modules.data.quality_cr import definition, register, validate_definition
from app.modules.data.quality_models import QualityCheck, QualityScheduleCursor
from app.modules.static.models import ObjectType
from app.modules.static.writeback import writeback_error

register()


class QualitySection:
    section = "qualityChecks"

    async def build_section(self, session, base, change_order_ids):
        rows = (await session.execute(select(QualityCheck, ObjectType).join(ObjectType))).all()
        return {
            c.code: {**definition(c, o), "ownerId": c.created_by, "retired": c.retired_at is not None}
            for c, o in rows
        }

    def seed_section(self, ver, ctx):
        return {}

    async def restore_section(self, session, content, *, release_id):
        # Restoring a historical definition must not silently authorize a previous timer.
        await session.execute(delete(QualityScheduleCursor))
        rows = {c.code: c for c in await session.scalars(select(QualityCheck).with_for_update())}
        for code, check in rows.items():
            if code not in content:
                check.retired_at = clock.now()
        for code, value in content.items():
            check = rows.get(code)
            if check is None:
                raise writeback_error("质量历史定义不存在，无法安全恢复", id=code)
            body, obj, limit = await validate_definition(session, value)
            if check.created_by != value.get("ownerId"):
                raise writeback_error("质量历史归属不一致", id=code)
            check.name, check.object_type_id, check.kind = body.name, obj.id, body.kind
            check.threshold, check.limit_value, check.schedule = body.threshold, limit, body.schedule
            check.field = body.field
            check.format_rule = body.format_rule
            check.retired_at = clock.now() if value.get("retired") else None
        await session.flush()


BUILDERS = [QualitySection()]
