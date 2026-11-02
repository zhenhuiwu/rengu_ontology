"""Offline boundary checks for assistant previews; no model or database required."""
import json
from types import SimpleNamespace

from app.modules.auto_ontology.assistant import accepted_history, clean_reply


def test_preview_rejects_unapproved_fields():
    assert clean_reply("function", {"draft": {"name": "score", "code": "def score(x):\n    return x"}})["draft"]["name"] == "score"
    for invalid in ({"draft": {"sql": "DROP TABLE x"}}, {"draft": {"timeoutS": 0}}, {"target": "action", "draft": {}}):
        try:
            clean_reply("function", invalid)
        except ValueError:
            continue
        raise AssertionError("invalid preview accepted")


def _item(role, content):
    return SimpleNamespace(role=role, content=content)


def test_follow_up_keeps_user_text_when_previous_preview_does_not_match():
    preview = json.dumps({"target": "metric", "message": "请补充公式", "draft": {}, "warnings": []}, ensure_ascii=False)
    history = accepted_history("dimension", [_item("user", "构建一个指标"), _item("assistant", preview), _item("assistant", "普通文本")], None)
    assert history == [{"role": "user", "content": "构建一个指标"}]


def test_follow_up_keeps_preview_for_the_same_target():
    preview = json.dumps({"target": "metric", "message": "请补充公式", "draft": {"name": "任务数"}, "warnings": []}, ensure_ascii=False)
    history = accepted_history("metric", [_item("user", "构建一个指标"), _item("assistant", preview)], None)
    assert json.loads(history[1]["content"])["draft"]["name"] == "任务数"
