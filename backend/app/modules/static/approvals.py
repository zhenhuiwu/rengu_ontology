"""Public read-only CR approval counts, keyed by stable assigned user code."""

from sqlalchemy import and_, func, or_, select

from app.modules.static.models import ChangeRequest, CRStatus


async def pending_approval_counts(session, codes: set[str], unique_names: dict[str, str]) -> dict[str, int]:
    reviewer = ChangeRequest.detail["reviewerCode"].as_string()
    rows = await session.execute(
        select(reviewer, ChangeRequest.reviewer, func.count())
        .where(
            ChangeRequest.status == CRStatus.in_review,
            or_(
                reviewer.in_(codes),
                and_(or_(reviewer.is_(None), reviewer == ""), ChangeRequest.reviewer.in_(unique_names)),
            ),
        )
        .group_by(reviewer, ChangeRequest.reviewer)
    )
    out = {}
    for code, name, count in rows:
        assigned = code if code else unique_names.get(name)
        if assigned in codes:
            out[assigned] = out.get(assigned, 0) + count
    return out
