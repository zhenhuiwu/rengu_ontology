"""Offline target-intent checks; no model, HTTP or database."""

from app.modules.qa.dynamic_ctx import identity_tokens, requested_action
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from sqlalchemy import Column, Integer, MetaData, String, Table
from app.modules.qa.dynamic_ctx import _identity_access
from app.core.errors import AppError

proposal = {"type": "update", "objKind": "Device", "objId": "1", "objLabel": "合成设备",
            "params": {"score": 90}}
assert identity_tokens("设备编号1，评分90")[0] == "1"
assert requested_action("设备编号1，评分90", proposal, [])[1] is None
assert requested_action("设备 ID: '12'，评分90", {**proposal, "objId": "12"}, [])[1] is None
assert requested_action("合成设备的评分改为90", proposal, [])[1], "unverified model labels are not identity evidence"
assert requested_action("编号11", proposal, [])[1], "1 must not match a substring of 11"
assert requested_action("编号1和编号2", proposal, [
    {"kind": "Device", "id": "1", "label": "one"}, {"kind": "Device", "id": "2", "label": "two"}
])[1]
print("PASS: explicit short identities, numeric parameter isolation, exact targets and ambiguity rejection")


async def guard():
    table = Table("inert", MetaData(), Column("id", Integer), Column("name", String))
    bound = SimpleNamespace(out=["id", "name"], cols=table.c, id_col=table.c.id, title="name", code="Device")
    for mask in ("none", "redact"):
        access = AsyncMock(return_value={"property": {"mask": mask}})
        with patch("app.modules.qa.explore_api._governance_access", AsyncMock()), patch("app.modules.governance.policy.enforce", access):
            try:
                await _identity_access(object(), object(), bound, filter_keys=["id"])
            except AppError:
                assert mask == "redact"
                assert access.await_count == 1, "masked titles cannot become identity evidence"
            else:
                assert mask == "none"
                assert access.await_args.args[3] == "filter" and access.await_args.kwargs["unmasked"]


asyncio.run(guard())
print("PASS: identity lookup requires readable titles and unmasked filter authority")
