"""Offline guard checks; intentionally not run as part of the local demo."""
from sqlglot import parse_one
from app.modules.qa.sqlfix import strip_unasked_enum


def test_unrequested_status_is_removed():
    tree = parse_one("SELECT count(*) FROM incidents WHERE status = 'open'", read="postgres")
    strip_unasked_enum(tree, {"status": {"open": "open"}}, "本月事件数", set())
    assert not tree.args.get("where")
    tree = parse_one("SELECT count(*) FROM incidents WHERE status = 'open'", read="postgres")
    strip_unasked_enum(tree, {"status": {"open": "open"}}, "open 事件数", set())
    assert tree.args.get("where")
