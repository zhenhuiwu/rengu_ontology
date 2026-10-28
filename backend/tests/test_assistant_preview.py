"""Offline boundary checks for assistant previews; no model or database required."""
from app.modules.auto_ontology.assistant import clean_reply


def test_preview_rejects_unapproved_fields():
    assert clean_reply("function", {"draft": {"name": "score", "code": "def score(x):\n    return x"}})["draft"]["name"] == "score"
    for invalid in ({"draft": {"sql": "DROP TABLE x"}}, {"draft": {"timeoutS": 0}}, {"target": "action", "draft": {}}):
        try:
            clean_reply("function", invalid)
        except ValueError:
            continue
        raise AssertionError("invalid preview accepted")
