"""取值索引 / 确定性 SQL 修正 / 反问拒答规则：纯单元测试，用一个非零售的虚构本体（车队运维）验证通用性。"""

import sqlglot

from app.modules.qa import guards, sqlfix
from app.modules.qa import value_index as vi


def _sem_rows():
    return [
        {"kind": "dimension", "api": "dim_org", "name": "组织"},
        {"kind": "dimension", "api": "dim_kind", "name": "车型"},
        {"kind": "dimValue", "id": "片区:N", "parent": "dim_org", "name": "北片区", "synonyms": ["北区"]},
        {"kind": "dimValue", "id": "片区:S", "parent": "dim_org", "name": "南片区", "synonyms": []},
        {"kind": "dimValue", "id": "片区:W", "parent": "dim_org", "name": "西片区", "synonyms": []},
        {"kind": "dimValue", "id": "大类:T", "parent": "dim_kind", "name": "货车", "synonyms": ["卡车"]},
        {"kind": "metric", "api": "trip_km", "name": "里程"},
    ]


def _idx():
    cols = [
        vi.Col("depot", "zone", "片区", values=["北片区", "南片区"]),
        vi.Col("depot", "name", "场站", values=["北京顺义场站", "天津港场站"], is_name=True, entity=True),
        vi.Col("vehicle", "kind", "车型", values=["货车/重卡", "货车/轻卡", "客车/大巴", "--"]),
        vi.Col("trip", "status", "状态", values=["done", "void"], labels={"完成": "done", "作废": "void"}),
        vi.Col("trip", "source", "来源", values=["调度台", "司机端"]),
    ]
    return vi.build(cols, _sem_rows())


def test_build_detects_path_and_binds_semantic_dims():
    idx = _idx()
    kind = idx.col("vehicle", "kind")
    assert kind.path and kind.placeholders == ["--"] and kind.segments(1) == {"货车", "客车"}
    zone = idx.col("depot", "zone")
    assert zone.dim == "dim_org" and zone.level == "片区"


def test_resolve_physical_synonym_label_and_path_segment():
    idx = _idx()
    res = idx.resolve("北区的卡车上周完成了多少趟")
    sqls = {h.sql() for h in res.unique()}
    assert "depot.zone = '北片区'" in sqls  # 维度同义词 → 物理取值
    assert "split_part(vehicle.kind, '/', 1) = '货车'" in sqls  # 路径列第 1 级
    assert "trip.status = 'done'" in sqls  # 枚举标签 → code
    assert any(line for line in vi.prompt_lines(res))


def test_resolve_missing_official_value_is_refused():
    idx = _idx()
    res = idx.resolve("西片区昨天的里程")
    assert not res.hits and [m.phrase for m in res.missing] == ["西片区"]
    r = guards.missing_value_refuse(res)
    assert r and r["code"] == "OUT_OF_SCOPE" and "西片区" in r["reason"]


def test_scope_conflict_refuse():
    idx = _idx()
    scope = idx.resolve("北片区").unique()
    assert guards.scope_conflict_refuse(scope, idx.resolve("南片区上周的里程"))["code"] == "OUT_OF_SCOPE"
    assert guards.scope_conflict_refuse(scope, idx.resolve("北片区上周的里程")) is None


def test_clarify_rules():
    idx = _idx()
    labels = {"来源": [idx.col("trip", "source")]}
    q = "电话来源的趟数"
    c = guards.unknown_value_clarify(q, idx, idx.resolve(q), labels)
    assert c and c["options"] == ["调度台", "司机端"]
    assert (
        guards.unknown_value_clarify("司机端来源的趟数", idx, idx.resolve("司机端来源的趟数"), labels) is None
    )
    c = guards.multi_time_clarify("昨天发车和到站的趟数", {"dep_at": ("发车",), "arr_at": ("到站",)}, {})
    assert c and c["options"] == ["dep_at", "arr_at"]
    c = guards.ambiguous_dim_clarify(
        "按地区看里程", idx, idx.resolve("按地区看里程"), ["大区", "片区", "车型"]
    )
    assert c and c["options"] == ["大区", "片区"]
    assert (
        guards.ambiguous_dim_clarify("按片区看里程", idx, idx.resolve("按片区看里程"), ["大区", "片区"])
        is None
    )
    opts = ["今天", "昨天"]
    assert guards.bare_metric_clarify("里程对比一下", vi.Resolution(), ["里程"], opts)
    assert guards.bare_metric_clarify("上周里程", vi.Resolution(), ["里程"], opts) is None
    assert guards.bare_metric_clarify("各场站里程", vi.Resolution(), ["里程"], opts) is None
    assert guards.bare_metric_clarify("删除所有表", vi.Resolution(), ["里程"], opts) is None


def _t(sql):
    return sqlglot.parse_one(sql, read="postgres")


def _s(tree):
    return tree.sql(dialect="postgres")


def test_sqlfix_like_to_eq_and_path_top_level_and_placeholders():
    idx = _idx()
    res = idx.resolve("北片区各车型的趟数")
    t = _t(
        "select d.zone, count(*) from trip t join depot d on d.id = t.depot_id"
        " where d.zone like '%北片区%' group by d.zone"
    )
    sqlfix.like_to_eq(t, res.unique())
    assert "d.zone = '北片区'" in _s(t)
    t = _t("with x as (select kind, count(*) c from vehicle group by kind) select kind, c from x")
    assert sqlfix.path_top_level(t, idx, [], {}) and "SPLIT_PART(kind, '/', 1) AS kind" in _s(t)
    t = _t("select v.kind, count(*) from vehicle v group by v.kind")
    assert sqlfix.exclude_placeholders(t, idx) and "NOT v.kind IN ('--')" in _s(t)


def test_sqlfix_time_enum_code_scope():
    t = _t(
        "select count(*) from trip t where t.status = 'done'"
        " and t.dep_at >= '2026-01-01' and t.dep_at < '2026-02-01'"
    )
    assert sqlfix.strip_time_window(t, frozenset({"dep_at"})) and "dep_at" not in _s(t)
    enum = {"status": {"done": "完成", "void": "作废"}}
    t = _t("select count(*) from trip t where t.status = 'done'")
    assert sqlfix.strip_unasked_enum(t, enum, "趟数", set()) and "status" not in _s(t)
    t = _t("select count(*) from trip t where t.status = 'done'")
    assert not sqlfix.strip_unasked_enum(t, enum, "趟数", {"done"})  # 口径要求的默认过滤保留
    t = _t("select count(*) from trip t where t.status = 'done'")
    assert not sqlfix.strip_unasked_enum(t, enum, "完成的趟数", set())
    t = _t("select d.name, count(*) from trip t join depot d on d.id = t.depot_id group by d.name")
    assert sqlfix.ensure_code(t, {"depot": {"name"}}, {"depot": "code"})
    assert _s(t).startswith("SELECT d.code, d.name") and "GROUP BY d.name, d.code" in _s(t)
    idx = _idx()
    scope = idx.resolve("北片区").unique()
    t = _t("select d.zone, count(*) from trip t join depot d on d.id = t.depot_id group by d.zone")
    assert sqlfix.scope_filter(t, scope) and "WHERE d.zone = '北片区'" in _s(t)


def test_fanout_violations():
    ch = {"trip": {"trip_leg"}, "depot": {"trip"}}
    bad = _t("select sum(t.fee), sum(l.km) from trip t join trip_leg l on l.trip_id = t.id")
    assert sqlfix.fanout_violations(bad, ch)
    bad = _t("select sum(l.km) / count(*) from trip t join trip_leg l on l.trip_id = t.id")
    v = sqlfix.fanout_violations(bad, ch)
    assert v and v[0].startswith("COUNT(*) 按 trip")
    ok = _t("select count(*), sum(t.fee) from trip t join depot d on d.id = t.depot_id")
    assert not sqlfix.fanout_violations(ok, ch)
    ok = _t("select count(distinct t.id), sum(l.km) from trip t join trip_leg l on l.trip_id = t.id")
    assert not sqlfix.fanout_violations(ok, ch)
    bad = _t("select sum(l.km) / nullif(count(*), 0) from trip_leg l join trip t on t.id = l.trip_id")
    assert sqlfix.fanout_violations(bad, ch)  # 明细合计 / COUNT(*)：FROM 明细表也要拦


def test_sqlfix_list_code_retarget_time_and_date_buckets():
    t = _t("select d.name, d.area from depot d where d.area > 300")
    assert sqlfix.ensure_code(t, {"depot": {"name"}}, {"depot": "code"}) == ["带上 depot.code"]
    assert t.sql(dialect="postgres").startswith("SELECT d.code, d.name")
    t = _t("select count(distinct d.name) from depot d")
    assert not sqlfix.ensure_code(t, {"depot": {"name"}}, {"depot": "code"})
    t = _t("select count(*) from trip t where t.arr_at >= '2026-01-02' and t.arr_at < '2026-01-03'")
    notes = sqlfix.retarget_time(t, "dep_at", frozenset({"dep_at", "arr_at"}), {"trip": {"dep_at", "arr_at"}})
    assert notes and "arr_at" not in t.sql()
    t = _t(
        "select date_trunc('day', t.dep_at) as d, date_trunc('hour', t.dep_at) as h, count(*)"
        " from trip t group by 1, 2"
    )
    assert sqlfix.date_buckets(t) and "CAST(DATE_TRUNC('DAY', t.dep_at) AS DATE)" in t.sql(dialect="postgres")
    assert "CAST(DATE_TRUNC('HOUR'" not in t.sql(dialect="postgres")


def test_overrule_model_clarify_with_defaults():
    idx = _idx()
    idx.level_names |= {"大类", "中类"}
    assert guards.overrule_clarify({"options": ["大类", "中类", "小类"]}, idx, [], {})
    scope = idx.resolve("北片区").unique()
    assert guards.overrule_clarify({"options": ["北片区", "南片区", "全部"]}, idx, scope, {})
    cal = {"里程": ["里程", "行驶里程"], "计费里程": ["计费里程"]}
    assert guards.overrule_clarify({"options": ["行驶里程", "计费里程"]}, idx, [], cal)
    assert guards.overrule_clarify({"options": ["调度台", "司机端"]}, idx, [], cal) is None
