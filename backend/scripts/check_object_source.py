"""Optional in-memory object reader boundaries; deliberately not executed during delivery.

No source connections, database sessions, HTTP, or persisted records.
Run later from backend: PYTHONPATH=. python scripts/check_object_source.py.
"""

from types import SimpleNamespace as Row

from app.core.errors import AppError
from app.modules.qa.explore_api import scope_operation
from app.modules.qa.object_source import check_object_read, identifier, primary_property


def main():
    check_object_read(Row(func={"object-search": "view"}))
    check_object_read(Row(func={"object-detail": "view"}))
    try:
        check_object_read(Row(func={}))
    except AppError as error:
        assert error.status_code == 403
    else:
        raise AssertionError("A reader without either page grant must be rejected")
    assert identifier("asset_id")
    assert not identifier("asset.id")
    assert not identifier("id;DROP TABLE asset")
    assert not identifier("../asset")
    prop = Row(api_name="id", source_column="asset_id", is_pk=True, is_sensitive=False,
               visibility="normal", data_type="integer", display_name="编号")
    assert primary_property(Row(pk_property="id", properties=[prop])) is prop
    assert primary_property(Row(pk_property="asset_id", properties=[prop])) is prop
    assert primary_property(Row(pk_property="unmapped", properties=[prop])) is None
    assert scope_operation("对象数量", [prop]) == ("count", None)
    assert scope_operation("编号合计", [prop]) == ("sum", "id")
    assert scope_operation("任意预测问题", [prop]) is None
    sensitive = Row(**{**vars(prop), "is_sensitive": True})
    hidden = Row(**{**vars(prop), "visibility": "hidden"})
    assert scope_operation("编号合计", [sensitive]) is None
    assert scope_operation("编号合计", [hidden]) is None
    assert scope_operation("编号合计", [prop, prop]) is None


if __name__ == "__main__":
    main()
