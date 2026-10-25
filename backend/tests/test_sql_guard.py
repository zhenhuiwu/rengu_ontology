import pytest

from app.common.sql_guard import SqlRejected, assert_single_select, inject_row_filters


@pytest.mark.parametrize(
    "sql",
    [
        "select 1; select 2",
        "delete from store",
        "with x as (delete from store returning *) select * from x",
        "select * into t2 from store",
        "select * from store for update",
        "select pg_sleep(10)",
        "select dblink('x','y')",
        "drop table store",
    ],
)
def test_rejects(sql):
    with pytest.raises(SqlRejected):
        assert_single_select(sql)


def test_inject_row_filters():
    out = inject_row_filters(
        "select s.name, sum(o.amount) from store s join biz_order o on o.store_id = s.id group by s.name",
        {"store": "region = '华东'", "biz_order": None},
    )
    assert "(SELECT * FROM store WHERE region = '华东') AS s" in out
    assert "JOIN biz_order AS o" in out


def test_inject_deny_all_and_unknown_table():
    assert "WHERE FALSE" in inject_row_filters("select * from store", {"store": "FALSE"})
    with pytest.raises(SqlRejected):
        inject_row_filters("select * from sys_user", {"store": None})
