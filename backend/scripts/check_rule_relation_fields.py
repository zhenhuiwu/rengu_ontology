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
    link = NS(ontology_id=16, code="task_asset", api_name="taskAsset", display_name="Task asset",
              status=DefStatus.published, via=None,
              from_code="Task", to_code="Asset", from_prop="asset_id", to_prop="id")
    objects = [NS(ontology_id=16, code="Task", status=ObjectStatus.published, properties=[prop("asset_id")]),
               NS(ontology_id=16, code="Asset", status=ObjectStatus.published, properties=[prop("id")])]
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
        fast = rule_relations.resolve_relation_binding(link.code, [link], objects, governed.values(), 16)
        assert all(a is b for a, b in zip(result, fast, strict=True))
        assert session.scalars.await_count == 2, "preloaded metadata must perform no SQL"
        assert "link_type.code =" in str(session.scalars.call_args_list[0].args[0])
        assert "link_type.api_name =" in str(session.scalars.call_args_list[0].args[0])
        assert "link_type.ontology_id =" in str(session.scalars.call_args_list[0].args[0])
        assert "object_type.code IN" in str(session.scalars.call_args_list[1].args[0])
        assert "object_type.ontology_id =" in str(session.scalars.call_args_list[1].args[0])
        # The cache remains tied to both transaction and ontology; no cross-request metadata cache is added.
        for change in ("transaction", "ontology"):
            if change == "transaction":
                transaction = object()
            else:
                session.info["ontology_id"] = link.ontology_id = 17
                for obj in objects:
                    obj.ontology_id = 17
            session.scalars = AsyncMock(side_effect=[[link], objects])
            changed = await rule_relations.read_relation_binding(session, link.code)
            assert changed is not result and session.scalars.await_count == 2
            assert await rule_relations.read_relation_binding(session, link.code) is changed
        session.info["ontology_id"] = link.ontology_id = 16
        for obj in objects:
            obj.ontology_id = 16
        await check_metadata(link, objects, governed)
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


async def check_metadata(link, objects, governed):
    from copy import copy
    from app.common.ontology import require_ontology_id
    from app.core.errors import AppError
    from app.modules.static.models import ObjectStatus

    # Run the actual meta relation block with one scoped catalog query and already-loaded object rows.
    start = next(i for i, n in enumerate(meta.body) if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "relations" for t in n.targets))
    fragment = ast.AsyncFunctionDef(
        name="relations", args=ast.arguments(posonlyargs=[], args=[ast.arg(arg="self"), ast.arg(arg="objects")],
                                             kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=[*meta.body[start:start + 2], ast.Return(value=ast.Name(id="relations", ctx=ast.Load()))],
        decorator_list=[],
    )
    module = ast.fix_missing_locations(ast.Module(body=[fragment], type_ignores=[]))
    namespace = {"require_ontology_id": require_ontology_id, "governed_tables": lambda *a: governed,
                 "AppError": AppError, "select": __import__("sqlalchemy").select}
    exec(compile(module, str(source), "exec"), namespace)
    user = NS(func={"link-types": "view", "object-search": "view"},
              row_filters={"Task": None, "Asset": None})

    async def metadata(links, object_rows=objects):
        scoped = NS(info={"ontology_id": 16}, scalars=AsyncMock(return_value=links))
        value = await namespace["relations"](NS(session=scoped, user=user), object_rows)
        assert scoped.scalars.await_count == 1
        sql = str(scoped.scalars.await_args.args[0])
        assert "link_type.ontology_id =" in sql and "link_type.status =" not in sql
        return value

    assert (await metadata([link]))[0]["id"] == link.code
    user.row_filters["Asset"] = {"deny": True}
    assert await metadata([link]) == []
    user.row_filters["Asset"] = None
    ambiguous = copy(link)
    ambiguous.code, ambiguous.api_name, ambiguous.status = "different", link.code, DefStatus.draft
    assert await metadata([link, ambiguous]) == [], "draft API/code collisions cannot be filtered out early"
    ambiguous_session = NS(info={"ontology_id": 16}, scalars=AsyncMock(return_value=[link, ambiguous]),
                           sync_session=NS(get_transaction=lambda: object()))
    try:
        await rule_relations.read_relation_binding(ambiguous_session, link.code)
    except AppError:
        pass
    else:
        raise AssertionError("normal binding accepted an API/code collision")
    assert ambiguous_session.scalars.await_count == 1, "ambiguous lookup must stop before endpoint reads"
    for mutation in ("sensitive", "hidden", "transform", "projection", "via", "draft", "wrong_scope",
                     "object_scope", "wrong_fk", "source_sql", "duplicate_binding", "missing_key"):
        altered, endpoints = copy(link), [copy(obj) for obj in objects]
        endpoints[1].properties = [copy(p) for p in endpoints[1].properties]
        key = endpoints[1].properties[0]
        if mutation == "sensitive": key.is_sensitive = True
        if mutation == "hidden": key.visibility = "hidden"
        if mutation == "transform": key.transform = "custom"
        if mutation == "via": altered.via = "bridge"
        if mutation == "draft": endpoints[1].status = ObjectStatus.draft
        if mutation == "wrong_scope": altered.ontology_id = 17
        if mutation == "object_scope": endpoints[1].ontology_id = 17
        if mutation == "projection": governed["parent"].projection = set()
        if mutation == "wrong_fk": governed["child"].table.c.asset_id.foreign_keys.clear()
        if mutation == "source_sql": governed["parent"].source_sql = "SELECT id FROM somewhere"
        if mutation == "duplicate_binding": governed["duplicate"] = governed["parent"]
        if mutation == "missing_key": key.source_column = "absent"
        assert await metadata([altered], endpoints) == [], mutation
        scoped = NS(info={"ontology_id": 16}, scalars=AsyncMock(side_effect=[[altered], endpoints]),
                    sync_session=NS(get_transaction=lambda: object()))
        try:
            await rule_relations.read_relation_binding(scoped, altered.code)
        except AppError:
            pass
        else:
            raise AssertionError(f"normal binding accepted {mutation}")
        if mutation == "projection": governed["parent"].projection = {"id"}
        if mutation == "wrong_fk":
            governed["child"].table.c.asset_id.append_foreign_key(ForeignKey("office_asset.id"))
        if mutation == "source_sql": governed["parent"].source_sql = None
        if mutation == "duplicate_binding": governed.pop("duplicate")


asyncio.run(check_binding())
print("Rule relation metadata/read bindings, real FK, publication, projection and transaction cache passed; no DB or evaluation.")
