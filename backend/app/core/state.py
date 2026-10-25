"""状态机校验：每个模块在 service.py 里写一张流转表，允许哪些流转照前端按钮的出现条件来。

TRANSITIONS: Transitions[CrStatus] = {
    CrStatus.draft: {CrStatus.in_review},
    CrStatus.in_review: {CrStatus.approved, CrStatus.rejected, CrStatus.draft},
}
ensure_transition(TRANSITIONS, cr.status, CrStatus.in_review)
"""

from collections.abc import Mapping, Set

from app.core.errors import AppError

type Transitions[S] = Mapping[S, Set[S]]


class InvalidTransition(AppError):
    status_code, code, message = 409, "INVALID_TRANSITION", "当前状态不允许该操作"


def ensure_transition[S](table: Transitions[S], cur: S, nxt: S) -> None:
    if nxt not in table.get(cur, ()):
        raise InvalidTransition(details={"from": str(cur), "to": str(nxt)})
