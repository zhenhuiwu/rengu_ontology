"""未配置的真实执行依赖不能返回测试回执或静默成功。"""

import pytest

from app.core.errors import AppError
from app.modules.dynamic.ports import UnconfiguredAdapters, get_submitter


async def test_default_adapters_report_unavailable():
    for call, code in (
        (lambda: get_submitter().submit("stored-action", "real-key"), "ACTION_EXECUTOR_UNAVAILABLE"),
        (lambda: UnconfiguredAdapters().check("stored-type", {}), "ACTION_CHECKER_UNAVAILABLE"),
        (
            lambda: UnconfiguredAdapters().emit("stored-event", "stored-type", {}),
            "EVENT_DELIVERY_UNAVAILABLE",
        ),
        (lambda: UnconfiguredAdapters().reopen_reconcile("stored-action"), "RECONCILE_UNAVAILABLE"),
    ):
        with pytest.raises(AppError) as error:
            await call()
        assert error.value.code == code and error.value.status_code == 503
