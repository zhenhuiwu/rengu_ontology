"""动作意图离线核对：用 palantir 的 20 题评测集（tests/fixtures/palantir_action_intents.yaml）检查问数的
意图解析（chain.parse_intent → intent.parse_action → policy_view / action_snapshot）。不调真模型：
“模型回复”按题目期望拼成问数提示词要求的 ```json {"action": …}``` 形状。"""

import json
from pathlib import Path

import yaml

from app.modules.qa import chain
from app.modules.qa import intent as act

EVALS = Path(__file__).parent / "fixtures" / "palantir_action_intents.yaml"
# 夹具本体的两个动作类型（同 tests/test_use_ops_runtime.py）；工具名 = action_<api>
CATALOG = [
    {
        "id": "ops_local",
        "api": "OpsLocal",
        "name": "标记测试门店",
        "targetKind": "store",
        "params": [{"api": "enabled", "name": "是否测试", "type": "布尔", "required": True}],
    },
    {
        "id": "ops_cross",
        "api": "OpsCross",
        "name": "跨门店标记测试",
        "targetKind": "store",
        "params": [
            {"api": "enabled", "name": "是否测试", "type": "布尔", "required": True},
            {"api": "otherStore", "name": "另一家门店", "type": "对象引用·store", "required": True},
        ],
    },
]
BY_TOOL = {f"action_{c['api']}": c for c in CATALOG}


def reply(type_id, args):
    body = {"action": {"type": type_id, **args, "reason": "评测"}}
    return f"```json\n{json.dumps(body, ensure_ascii=False)}\n```"


def cases():
    data = yaml.safe_load(EVALS.read_text(encoding="utf-8"))
    assert len(data["cases"]) == 20
    return data["cases"]


def test_action_cases_parse_to_the_expected_action():
    checked = 0
    for case in cases():
        exp = case["expect"]
        tool = BY_TOOL.get(exp.get("tool", ""))
        if exp["kind"] not in ("call", "confirm") or tool is None:
            continue  # 事件 / 函数工具问数不做（走 MCP 导出的工具）
        kind, val = chain.parse_intent(reply(tool["id"], exp["args"]))
        assert kind == "action", case["id"]
        got = act.parse_action(val, CATALOG)
        assert got is not None, case["id"]
        assert (got["type"], got["objKind"], got["objId"]) == (
            tool["id"],
            exp["args"]["objKind"],
            exp["args"]["objId"],
        ), case["id"]
        assert got["params"] == exp["args"]["params"], case["id"]
        # 动作名也能认（模型偶尔回名称而不是编号）
        assert act.parse_action({"action": {"type": tool["name"]}}, CATALOG)["type"] == tool["id"]
        checked += 1
    assert checked == 8


def test_unknown_or_missing_targets():
    by_id = {c["id"]: c for c in cases()}
    # refuse-01 / 02：没有对应动作 → 不成动作意图（链路按 NOT_SELECT 拒答）
    for cid in ("refuse-01", "refuse-02"):
        assert act.parse_action({"action": {"type": "delete_store", "objId": "OPS-S01"}}, CATALOG) is None, (
            cid
        )
    # clarify-01：缺目标对象 → 卡片留空由用户在确认卡里补，暂存时 422
    assert by_id["clarify-01"]["expect"]["missing"] == ["objId"]
    got = act.parse_action({"action": {"type": "ops_local", "params": {"enabled": True}}}, CATALOG)
    assert (
        got["objId"] is None
        and act.action_snapshot(got, {"passed": False})["answer"]["action"]["stage"]["objId"] == ""
    )
    # 多余参数丢掉，不带进卡片
    got = act.parse_action({"action": {"type": "ops_local", "params": {"enabled": 1, "x": 2}}}, CATALOG)
    assert got["params"] == {"enabled": 1}


def test_policy_cases_never_auto_execute():
    by_id = {c["id"]: c for c in cases()}
    intent = act.parse_action({"action": {"type": "ops_local", "objId": "OPS-S01"}}, CATALOG)
    for cid in ("refuse-03", "refuse-05", "call-07", "confirm-01", "confirm-02", "confirm-03"):
        raw = (by_id[cid].get("setup") or {}).get("policy")
        policy = act.policy_view(raw)
        card = act.action_snapshot(intent, {"passed": True}, policy)["answer"]["action"]
        assert card["mode"] == "suggest", cid  # 问数只出建议卡，发起 = 暂存待人工确认
        if cid == "refuse-03":
            assert policy["mode"] == "suggest" and card["canStage"] is False
        elif cid == "refuse-05":  # 策略只允许规则发起：问数（AI）不能暂存
            assert policy["aiAllowed"] is False and card["canStage"] is False
        else:
            assert card["canStage"] is (policy["aiAllowed"] is not False), cid
