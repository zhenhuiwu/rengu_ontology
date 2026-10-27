"""Optional pure in-memory relation check; no database, HTTP or persisted model changes.

Not executed during delivery because backend tests are prohibited.
Run later from backend with PYTHONPATH=. python scripts/check_link_endpoints.py.
"""

from app.modules.static.link_validation import link_error


def main():
    objects = [{"id": "a"}, {"id": "b"}]
    properties = {"a": [{"api": "b_id", "type": "int"}], "b": [{"api": "id", "type": "integer"}]}
    relation = {"name": "关联", "api": "relates_to", "from": "a", "to": "b", "card": "N : 1",
                "fromProp": "b_id", "toProp": "id", "nullable": False}
    assert link_error(relation, objects, properties, is_new=True) is None
    assert link_error({**relation, "to": "missing"}, objects, properties, is_new=True)
    assert link_error({**relation, "fromProp": "missing"}, objects, properties, is_new=True)
    incompatible = {**properties, "b": [{"api": "id", "type": "string"}]}
    assert link_error(relation, objects, incompatible, is_new=True)


if __name__ == "__main__":
    main()
