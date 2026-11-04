"""One async execution kernel; legacy local call sites retain their annotation policy."""

import asyncio
import time

from app.core.errors import AppError
from app.modules.functions import remote, runtime
from app.modules.functions.remote_schema import signature as remote_signature


def definition_signature(snapshot):
    if snapshot.get("runtimeMode", "local") == "remote_http":
        return remote_signature(snapshot["remote"])
    return runtime.signature(snapshot["code"], snapshot["name"])


async def authorize(session, user, code):
    from app.modules.governance.policy import enforce
    from app.modules.static.lineage import identity
    await enforce(session, user, [identity("function", code)], "invoke")


class InvocationBudget:
    def __init__(self, *, remote_calls=False):
        settings = remote.RemoteSettings() if remote_calls else None
        self.limit = settings.function_rule_max_calls if settings else 40_004
        self.seconds = settings.function_rule_timeout_s if settings else 2
        self.used, self.deadline = 0, None

    def require_capacity(self, count):
        if count > self.limit:
            raise AppError("规则函数调用超过整轮预算，请缩小试跑范围",
                           code="FUNCTION_RULE_LIMIT", status_code=422)

    async def run(self, call):
        self.require_capacity(self.used + 1)
        if self.deadline is None:
            self.deadline = time.monotonic() + self.seconds
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise AppError("规则函数整轮时间预算已用尽", code="FUNCTION_RULE_LIMIT", status_code=422)
        self.used += 1
        try:
            async with asyncio.timeout(remaining):
                return await call()
        except TimeoutError:
            raise AppError("规则函数整轮时间预算已用尽",
                           code="FUNCTION_RULE_LIMIT", status_code=422) from None


async def invoke_snapshot(snapshot, inputs, *, ontology_id, code, version, strict_local=False):
    runtime.json_value(inputs)
    mode = snapshot.get("runtimeMode", "local")
    if mode == "remote_http":
        return await remote.invoke(
            ontology_id, code, version, snapshot["remote"], inputs, snapshot["timeoutS"]
        )
    if mode != "local":
        raise AppError("函数执行模式不可用", code="FUNCTION_VERSION_UNAVAILABLE", status_code=409)
    declared, output = definition_signature(snapshot)
    if strict_local:
        from app.contracts.functions import typed
        for field in declared:
            if field["name"] in inputs:
                typed(inputs[field["name"]], field["type"])
    result = runtime.run(snapshot["code"], snapshot["name"], inputs, snapshot["timeoutS"])
    if strict_local:
        typed(result, output["type"])
    return result
