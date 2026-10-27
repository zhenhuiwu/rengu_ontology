"""Optional in-memory source/candidate check, no database or HTTP.

Not executed during delivery: backend tests are prohibited.
Run later from backend: PYTHONPATH=. python scripts/check_standalone_code_tables.py.
"""

from app.modules.static.dimension_validation import dimension_source_error, query_dimension_names


def main():
    assert dimension_source_error("码表", "", set()) is None
    assert dimension_source_error("维度", "", set())
    assert dimension_source_error("码表", "missing", {"asset"})
    assert dimension_source_error("码表", "", set(), attr="status")
    assert dimension_source_error("码表", "", set(), column="asset.status")
    assert dimension_source_error("维度", "asset", {"asset"}, attr="status") is None
    assert dimension_source_error("码表", "asset", {"asset"}) is None
    dimensions = [
        {"name": "状态码表", "object": "", "status": "已发布", "levels": [{"name": "设备状态"}]},
        {"name": "区域", "object": "asset", "status": "已发布", "levels": [{"name": "区域"}]},
    ]
    metrics = [{"dimNames": ["状态码表", "设备状态", "区域", "已有维度"]}]
    assert query_dimension_names(dimensions, metrics) == ["区域", "已有维度"]
    # Historical snapshots use the same source guard; a name shared with a bound
    # dimension stays legal, and empty catalogs never borrow current definitions.
    bound = {**dimensions[0], "object": "asset"}
    assert query_dimension_names([bound], metrics) == ["设备状态", "状态码表", "区域", "已有维度"]
    assert query_dimension_names([dimensions[0], bound], metrics) == query_dimension_names([bound], metrics)
    assert query_dimension_names([], []) == []


if __name__ == "__main__":
    main()
