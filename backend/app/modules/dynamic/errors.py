"""动态新增错误码（dynamic-api.md §4）。通用码直接用 app.core.errors / app.core.state。"""

from app.core.errors import AppError


class NotConfirmer(AppError):
    """动作有 source.confirmer 时，非该确认人确认提交 / 撤回。"""

    status_code, code, message = 403, "NOT_CONFIRMER", "只有确认人可以确认提交或撤回"


class NotAssignee(AppError):
    """非处理人处理告警（关闭 / 发起动作）。"""

    status_code, code, message = 403, "NOT_ASSIGNEE", "只有处理人可以处理该告警"


class AlertLockedByAction(AppError):
    status_code, code, message = 409, "ALERT_LOCKED_BY_ACTION", "关联动作进行中，暂不能转派或关闭"


class AlreadyResubmitted(AppError):
    status_code, code, message = 409, "ALREADY_RESUBMITTED", "该动作已重新提交过"
