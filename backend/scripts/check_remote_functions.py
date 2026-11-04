"""In-memory remote function contract check; no DB, sockets, model, or real HTTP."""

import ast
import asyncio
import json
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.contracts import functions as contracts
from app.core.errors import AppError
from app.modules.dynamic.event_rule_runtime import EventExpression
from app.modules.dynamic.metric_rules import MetricExpression
from app.modules.dynamic.rule_runtime import Expression, parse_expression
from app.modules.functions import executor, remote, router, service
from app.modules.functions.remote_schema import validate_schema, validate_value
from app.modules.functions.schemas import CreateIn, TestIn
from app.modules.machine.router import function_invoke


async def rejected(call, code):
    try:
        await call()
    except AppError as error:
        assert error.code == code, (error.code, code)
        return error
    raise AssertionError(f"Expected {code}")


def target():
    return remote.Target.model_validate({
        "ontologyId": 17, "key": "office_score", "label": "合成计算", "revision": "r1",
        "url": "https://compute.invalid/calculate", "networks": ["127.0.0.0/8"],
        "token": "synthetic-only", "pure": True, "timeoutS": 2,
        "inputSchema": {"type": "object", "properties": {"value": {"type": "integer"}},
                        "required": ["value"], "additionalProperties": False},
        "outputSchema": {"type": "integer"},
    })


async def main():
    configured = target()
    public = configured.public()
    assert not {"url", "token", "networks", "origin"} & public.keys()
    body = CreateIn(name="remote_score", title="合成计算", runtime_mode="remote_http",
                    remote={"targetKey": configured.key, "revision": configured.revision}, timeout_s=2)
    with patch.object(remote, "targets", return_value=[configured]):
        execution = service.execution_fields(body, 17)
    snapshot = {"name": body.name, "code": "", "timeoutS": 2, **execution}
    assert executor.definition_signature(snapshot)[0][0]["type"] == "int"
    for bad in (True, 1.5, "1", None):
        try:
            validate_value({"value": bad}, configured.input_schema)
        except AppError:
            pass
        else:
            raise AssertionError("Schema coerced a value")
    for schema in ({"type": "object", "properties": {}}, {"type": "array"},
                   {"type": "string", "$ref": "https://invalid"}):
        try:
            validate_schema(schema)
        except AppError:
            pass
        else:
            raise AssertionError("Unsafe schema accepted")

    requests = []
    outcome = {"mode": "ok"}

    async def handler(request):
        requests.append(request)
        payload = json.loads(request.content)
        assert payload == {"functionId": "FN-SYNTHETIC", "version": "v1.0.0",
                           "revision": "r1", "input": {"value": 3}}
        assert request.headers["X-Ontology-Id"] == "17"
        assert request.headers["Authorization"] == "Bearer synthetic-only"
        if outcome["mode"] == "timeout":
            raise httpx.ReadTimeout("synthetic", request=request)
        if outcome["mode"] == "wait":
            await asyncio.Event().wait()
        if outcome["mode"] == "http":
            return httpx.Response(503, json={"error": "synthetic"})
        if outcome["mode"] == "oversize":
            return httpx.Response(200, content=b"x" * 65_537, headers={"Content-Type": "application/json"})
        return httpx.Response(200, json={
            "revision": "r2" if outcome["mode"] == "revision" else "r1",
            "output": True if outcome["mode"] == "schema" else 6,
        })

    client_type = httpx.AsyncClient

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return client_type(**kwargs, transport=httpx.MockTransport(handler))

    async def invoke():
        return await executor.invoke_snapshot(snapshot, {"value": 3}, ontology_id=17,
                                              code="FN-SYNTHETIC", version="v1.0.0")

    with (
        patch.object(remote, "targets", side_effect=lambda ontology: [configured] if ontology == 17 else []),
        patch.object(remote, "pinned", AsyncMock(return_value=(
            "https://127.0.0.1:443/calculate", "compute.invalid", "compute.invalid"))),
        patch.object(remote.httpx, "AsyncClient", side_effect=client),
    ):
        assert await invoke() == 6 and len(requests) == 1
        for mode, code in (("http", "FUNCTION_REMOTE_HTTP_ERROR"),
                           ("revision", "FUNCTION_REMOTE_INVALID_RESPONSE"),
                           ("schema", "FUNCTION_REMOTE_INVALID_RESPONSE"),
                           ("oversize", "FUNCTION_REMOTE_INVALID_RESPONSE"),
                           ("timeout", "FUNCTION_REMOTE_TIMEOUT")):
            before = len(requests)
            outcome["mode"] = mode
            await rejected(invoke, code)
            assert len(requests) == before + 1  # Never a second POST.
        outcome["mode"] = "wait"
        task = asyncio.create_task(invoke())
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("Cancellation swallowed")
        outcome["mode"] = "ok"
        before = len(requests)
        await rejected(lambda: executor.invoke_snapshot(snapshot, {"value": 3}, ontology_id=18,
                        code="FN-SYNTHETIC", version="v1.0.0"), "FUNCTION_REMOTE_UNAVAILABLE")
        changed = deepcopy(snapshot)
        changed["remote"]["bindingDigest"] = "changed"
        await rejected(lambda: executor.invoke_snapshot(changed, {"value": 3}, ontology_id=17,
                        code="FN-SYNTHETIC", version="v1.0.0"), "FUNCTION_REMOTE_UNAVAILABLE")
        assert len(requests) == before

        session = SimpleNamespace(info={"ontology_id": 17}, scalar=AsyncMock(return_value="published"))
        detail = {**snapshot, "id": "FN-SYNTHETIC", "version": "v1.0.0"}
        with (patch.object(service, "detail", AsyncMock(return_value=detail)),
              patch.object(router, "authorize", AsyncMock()),
              patch.object(executor, "authorize", AsyncMock())):
            test = await router.test_function("FN-SYNTHETIC", TestIn(input={"value": 3}), session, None)
            machine = await function_invoke("FN-SYNTHETIC", TestIn(input={"value": 3}), session, None)
            assert test["output"] == machine["output"] == 6
        session.execute = AsyncMock(return_value=SimpleNamespace(one_or_none=lambda: (
            SimpleNamespace(status="published"), SimpleNamespace(snapshot=snapshot))))
        with patch.object(contracts, "check_func"), patch.object(contracts, "authorize", AsyncMock()):
            assert await contracts.invoke_version(session, None, "FN-SYNTHETIC", "v1.0.0", {"value": 3}) == 6

        node = parse_expression('rule_fn("FN-SYNTHETIC", "v1.0.0", 3) > 0')
        call = next(item for item in ast.walk(node) if isinstance(item, ast.Call))
        bound = {**snapshot, "_functionCode": "FN-SYNTHETIC", "_functionVersion": "v1.0.0", "_ontologyId": 17}
        expression = Expression(node, None, None, functions={ast.dump(call): (bound, ["value"])})
        assert (await expression.run({}, datetime(2026, 1, 1, tzinfo=UTC)))[0] is True
        expression.function_budget.limit = 1
        before = len(requests)
        await rejected(lambda: expression.run({}, datetime(2026, 1, 1, tzinfo=UTC)),
                       "FUNCTION_RULE_LIMIT")
        assert len(requests) == before
        await rejected(lambda: expression.function_budget.run(invoke), "FUNCTION_RULE_LIMIT")
        for expression_type in ("event", "metric"):
            tree = parse_expression('rule_fn("FN-SYNTHETIC", "v1.0.0", 3) > 0')
            functions = {ast.dump(call): (bound, ["value"])}
            if expression_type == "event":
                child = EventExpression(tree, {}, {}, {"payload": []}, functions=functions)
            else:
                child = MetricExpression(tree, None, None, {}, functions, {})
            assert (await child.run({}, datetime(2026, 1, 1, tzinfo=UTC)))[0] is True

    # Legacy local annotations remain advisory for test/machine and strict for action bindings.
    local = {"name": "calc", "code": "def calc(x: Literal['ok']) -> str:\n    return x", "timeoutS": 5}
    result = await executor.invoke_snapshot(local, {"x": "ok"}, ontology_id=17, code="FN-L", version="v1")
    assert result == "ok"
    await rejected(lambda: executor.invoke_snapshot(local, {"x": "ok"}, ontology_id=17,
                   code="FN-L", version="v1", strict_local=True),
                   "FUNCTION_TYPE_UNSUPPORTED")
    with patch.object(asyncio.get_running_loop(), "getaddrinfo", AsyncMock(return_value=[
        (0, 0, 0, "", ("169.254.169.254", 443))
    ])):
        await rejected(lambda: remote.pinned(configured), "FUNCTION_REMOTE_UNAVAILABLE")
    print("Remote functions passed: strict schema, revision/scope/policy seams, async execution, "
          "rule budget, no retry, cancellation, bounded responses, legacy local compatibility.")


if __name__ == "__main__":
    asyncio.run(main())
