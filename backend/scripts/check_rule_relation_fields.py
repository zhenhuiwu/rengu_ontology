"""Check the actual relation-field projection expression without DB or rule execution."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from sqlalchemy import Column, ForeignKey, Integer, MetaData, Table
from app.modules.dynamic import rule_relations
from app.modules.static.models import DefStatus, ObjectStatus


source = Path(__file__).resolve().parents[1] / "app/modules/dynamic/rules_service.py"
tree = ast.parse(source.read_text("utf-8"))
meta = next(node for cls in tree.body if isinstance(cls, ast.ClassDef) and cls.name == "RuleService"
            for node in cls.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "meta")
expression = next(node.value for node in ast.walk(meta) if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == "fields" for target in node.targets))
assert isinstance(expression, ast.ListComp)
compiled = compile(ast.Expression(expression), str(source), "eval")


def prop(api, **patch):
    return NS(**{"api_name": api, "display_name": api, "backing_column": None,
                 "source_column": api, "is_sensitive": False, "visibility": "normal", "transform": None, **patch})


dst = NS(properties=[
    prop("source_only"), prop("mapped", backing_column="physical", source_column="absent"),
    prop("hidden", visibility="hidden"), prop("secret", is_sensitive=True),
    prop("transformed", transform="custom"), prop("unapproved"), prop("missing"),
])
columns = {"source_only", "physical", "hidden", "secret", "transformed", "unapproved"}
gov = NS(table=NS(c=columns), projection={"source_only", "physical", "hidden", "secret", "transformed"})
assert eval(compiled, {"dst": dst, "gov": gov}) == [
    {"api": "source_only", "name": "source_only"}, {"api": "mapped", "name": "mapped"},
]
gov.projection = None
assert [field["api"] for field in eval(compiled, {"dst": dst, "gov": gov})] == ["source_only", "mapped", "unapproved"]

async def check_binding():
    # Separate reflected MetaData collections deliberately leave FK.column unresolved.
    child = Table("office_task", MetaData(), Column("asset_id", Integer, ForeignKey("office_asset.id")))
    parent = Table("office_asset", MetaData(), Column("id", Integer, primary_key=True))
    link = NS(code="task_asset", api_name="taskAsset", status=DefStatus.published, via=None,
              from_code="Task", to_code="Asset", from_prop="asset_id", to_prop="id")
    objects = [NS(code="Task", status=ObjectStatus.published, properties=[prop("asset_id")]),
               NS(code="Asset", status=ObjectStatus.published, properties=[prop("id")])]
    governed = {
        "child": NS(object_type="Task", table=child, columns={"asset_id": child.c.asset_id}, projection={"asset_id"}, inherit=None, source_sql=None, links={}),
        "parent": NS(object_type="Asset", table=parent, columns={"id": parent.c.id}, projection={"id"}, inherit=None, source_sql=None, links={}),
    }
    transaction = object()
    session = NS(info={"ontology_id": 16}, scalars=AsyncMock(side_effect=[[link], objects]),
                 sync_session=NS(get_transaction=lambda: transaction))
    with patch.object(rule_relations, "ensure_governed_tables", AsyncMock()), patch.object(rule_relations, "governed_tables", return_value=governed):
        result = await rule_relations.read_relation_binding(session, link.code)
        assert result[-2] is child.c.asset_id and result[-1] is parent.c.id
        assert session.scalars.call_args_list[0].args[0]._for_update_arg is None
        assert await rule_relations.read_relation_binding(session, link.code) is result
        assert session.scalars.await_count == 2, "same relation fields must reuse transaction-local binding"
        for mutation in ("wrong_fk", "projection", "unpublished"):
            session.info.pop("rule_relation_read_context", None)
            session.scalars = AsyncMock(side_effect=[[link], objects])
            child.c.asset_id.foreign_keys.clear() if mutation == "wrong_fk" else None
            if mutation == "projection": governed["parent"].projection = set()
            if mutation == "unpublished": link.status = DefStatus.draft
            try:
                await rule_relations.read_relation_binding(session, link.code)
            except Exception as exc:
                from app.core.errors import AppError
                assert isinstance(exc, AppError)
            else:
                raise AssertionError("invalid FK, projection or unpublished metadata must be rejected")
            if mutation == "wrong_fk": child.c.asset_id.append_foreign_key(ForeignKey("office_asset.id"))
            if mutation == "projection": governed["parent"].projection = {"id"}


asyncio.run(check_binding())
print("Rule relation metadata/read bindings, real FK, publication, projection and transaction cache passed; no DB or evaluation.")
