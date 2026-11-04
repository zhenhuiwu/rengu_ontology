"""Compare current/historical overview cards with the former full-directory reads, offline."""

import ast
import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

SOURCE_PATH = Path(__file__).resolve().parents[1] / "app/modules/static/overview.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")
FORMER_READS = """
await svc._check_ver()
objects = [shapes.object_list_item(o, 0) for o in await svc.repo.object_types()]
metrics = [shapes.metric_list_item(m) for m in await svc.repo.metrics()]
if svc.ver:
    objects = list((await svc._section("objectTypes") or {}).values())
    metrics = list((await svc._section("metrics") or {}).values())
links = await svc._links()
dims = [d for d in await svc._dims() if d.get("status") != "已驳回"]
"""


async def check():
    tree = ast.parse(SOURCE)
    current = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "overview")
    current.returns = None
    for arg in current.args.args:
        arg.annotation = None
    former = deepcopy(current)
    former.name = "former_overview"
    tail = next(
        i
        for i, node in enumerate(former.body)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "actions" for t in node.targets)
    )
    former.body = ast.parse(FORMER_READS).body + former.body[tail:]
    models = {
        name: MagicMock(name=name)
        for name in ("Dimension", "ChangeRequest", "CrTarget", "ActionRun", "Event", "EventType")
    }
    objects = [{"id": "O1", "status": "已发布"}, {"id": "O2", "status": "草稿"}]
    metrics = [{"id": "M1", "status": "已发布"}, {"id": "M2", "status": "已下线"}]
    dimensions = [
        {"id": "D1", "kind": "维度", "status": "已发布"},
        {"id": "D2", "kind": "码表", "status": "变更中"},
        {"id": "D3", "kind": "维度", "status": "已驳回"},
    ]
    links = [
        {"id": "L1", "status": "已发布", "quality": {"orphans": 1}},
        {"id": "L2", "status": "草稿", "quality": {"orphans": 0}},
    ]

    class Query:
        def __init__(self, columns):
            self.columns = columns

        def __getattr__(self, name):
            return lambda *args, **kwargs: self

    class Rows(list):
        def all(self):
            return list(self)

    class Service:
        def __init__(self, ver):
            self.ver = ver
            self.ctx = SimpleNamespace(ver=ver)
            self.repo = self
            self.session = self
            self.reads = []

        async def _check_ver(self):
            self.reads.append("version checked")

        async def object_types(self, *, light=False):
            self.reads.append(("objects", light))
            return objects

        async def metrics(self):
            self.reads.append("metrics")
            return metrics

        async def _section(self, name):
            return {d["id"]: d for d in (objects if name == "objectTypes" else metrics)}

        async def _links(self):
            return links

        async def _dims(self):
            self.reads.append("full dimensions")
            return dimensions

        async def execute(self, query):
            if query.columns[0] is models["Dimension"].code:
                assert query.columns == (
                    models["Dimension"].code,
                    models["Dimension"].kind,
                    models["Dimension"].status,
                )
                self.reads.append("dimension heads")
                return Rows(
                    (d["id"], SimpleNamespace(value=d["kind"]), SimpleNamespace(value=d["status"]))
                    for d in dimensions
                )
            if query.columns[0] is models["CrTarget"].target_kind:
                return Rows([("object", "O2"), ("object", "O3"), ("metric", "M3"), ("dimension", "D2")])
            assert query.columns[0] is models["ActionRun"].status
            return Rows([("unknown", 2), ("failed", 3)])

        async def scalar(self, query):
            return 4

    async def actions(ctx):
        return [{"id": "A1", "status": "已发布"}, {"id": "A2", "status": "变更中"}]

    async def events(ctx):
        return [{"id": "E1", "status": "已发布"}]

    async def quality(svc, user):
        return ["check1", "check2"], {1: SimpleNamespace(result="失败")}

    env = {
        **models,
        "select": lambda *columns: Query(columns),
        "func": MagicMock(),
        "shapes": SimpleNamespace(object_list_item=lambda o, count: o, metric_list_item=lambda m: m),
        "ActionTypeService": lambda session: SimpleNamespace(defs=actions),
        "EventTypeService": lambda session: SimpleNamespace(defs=events),
        "quality_state": quality,
        "CRStatus": SimpleNamespace(draft="draft", in_review="review", approved="approved"),
        "ActionStatus": SimpleNamespace(unknown="unknown", failed="failed"),
        "EventOutcome": SimpleNamespace(failed="failed"),
        "clock": SimpleNamespace(now=lambda: "now"),
        "_fmt_dt": lambda value: value,
    }
    exec(compile(ast.Module(body=[current, former], type_ignores=[]), str(SOURCE_PATH), "exec"), env)
    for ver in (None, "v1"):
        svc, baseline = Service(ver), Service(ver)
        result = await env["overview"](svc, object())
        assert result == await env["former_overview"](baseline, object())
        assert result["cards"][0]["health"][0]["n"] == (2 if ver is None else 1)
        assert [c["count"] for c in result["cards"]] == [1, 1, 2, 1, 1, 2]
        assert svc.reads[0] == "version checked"
        if ver is None:
            assert ("objects", True) in svc.reads
            assert "dimension heads" in svc.reads and "full dimensions" not in svc.reads
        else:
            assert svc.reads == ["version checked", "full dimensions"]


if __name__ == "__main__":
    asyncio.run(check())
    print("Overview summary read passed: current/historical card equivalence and narrower directory reads.")
