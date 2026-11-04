"""Offline checks of supplemental definition selection; no application/DB imports."""

import ast
import json
import re
from copy import deepcopy
from pathlib import Path


def check():
    path = Path(__file__).resolve().parents[1] / "app/modules/qa/definition_answers.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name in {"select_evidence", "prompt_lines", "snapshot"}
    ]
    assert len(nodes) == 3
    env = {"json": json, "re": re}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), env)
    select, lines = env["select_evidence"], env["prompt_lines"]
    long_text = "业务知识「温控规范」：" + "完整说明，" * 1000
    items = {
        "object:AssetType": "对象类型「设备档案」：已发布说明。",
        "action:ACT-001": "动作「发起巡检」：目标对象：设备档案。提交条件：温控规范。",
        "link:LINK-001": "关系「设备所属」：设备档案 → 责任部门。",
        "knowledge:KN-001": long_text,
        "metric:MET-001": "指标「检查数量」：公式 COUNT(*)。",
    }
    original = deepcopy(items)
    expected = {
        k: v for k, v in items.items() if k in {"object:AssetType", "action:ACT-001", "link:LINK-001"}
    }
    assert select("设备档案有哪些关联与操作？", items) == expected
    assert select("assettype 的定义", items) == expected
    assert select("ACT-001 的提交条件", items) == {"action:ACT-001": items["action:ACT-001"]}
    assert select("KN-001 的内容", items) == {k: v for k, v in items.items() if "温控规范" in v}
    for question in (
        "未知概念是什么",
        "ACT-0019是什么",
        "XACT-001是什么",
        "ACT-001-extra是什么",
        "系统有哪些能力支持设备档案",
        "列出全部设备档案相关的定义",
        "本体能力与设备档案",
        "设备档案与检查数量的定义",
        "list all capabilities for AssetType",
    ):
        assert select(question, items) == items, question
    duplicate = {**items, "knowledge:OTHER-001": "业务知识「设备档案」：另一个定义。"}
    assert select("设备档案是什么", duplicate) == duplicate
    overlapping = {**items, "object:OTHER-001": "对象类型「设备」：说明。"}
    assert select("设备档案是什么", overlapping) == overlapping
    short = {"object:A": "对象类型「表」：说明。", "object:AB": "对象类型「ID」：说明。"}
    assert select("表是什么", short) == short
    assert select("ID是什么", short) == short
    assert select("AB是什么", short) == short
    assert select("未知", {}) == {}
    assert items == original
    assert dict(json.loads(line) for line in lines(items)) == items
    assert json.loads(lines({"knowledge:KN-001": long_text})[0])[1] == long_text
    narrowed = select("设备档案是什么", items)
    snapshot = env["snapshot"]
    assert snapshot(["object:AssetType"], narrowed) == snapshot(["object:AssetType"], items)
    assert snapshot(["object:AssetType", "object:AssetType"], narrowed) == snapshot(
        ["object:AssetType"], items
    )
    assert snapshot(["knowledge:KN-001"], narrowed) is None
    assert snapshot(["unknown"], narrowed) is None
    assert snapshot([], narrowed) is None
    assert snapshot(["object:AssetType"] * 6, narrowed) is None
    narrowed.clear()
    assert items == original


if __name__ == "__main__":
    check()
    print(
        "QA definition context passed: anchors, ID boundaries, ambiguity/fallbacks, "
        "related items and full bodies."
    )
