"""Public read-only counts for actions explicitly assigned to stable approver codes."""

from sqlalchemy import func, select

from app.modules.dynamic.models import ActionRun, ActionStatus


async def pending_approval_counts(session, codes: set[str]) -> dict[str, int]:
    return dict(
        (
            await session.execute(
                select(ActionRun.approver, func.count())
                .where(ActionRun.status == ActionStatus.approving, ActionRun.approver.in_(codes))
                .group_by(ActionRun.approver)
            )
        ).all()
    )
