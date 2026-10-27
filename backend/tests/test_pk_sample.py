"""Finite real-PG samples of a draft candidate key, preserving source facts."""

import json

import asyncpg

from app.common.auth import resolve_user
from app.modules.static.pk_sample import SAMPLE_LIMIT, sample_primary_key, sample_result
from tests import test_object_source_regression as support

real_source = support.real_source
source_case = support.source_case


def test_empty_null_and_truncated_samples_never_certify_whole_source():
    assert sample_result([])[0] == "警告"
    assert sample_result([None, ""])[0] == "阻断"
    assert sample_result(["a", "a"])[0] == "阻断"
    status, note = sample_result([1, 2], truncated=True)
    assert status == "通过" and str(SAMPLE_LIMIT) in note and "不代表全源唯一" in note


async def test_real_duplicate_code_and_draft_integer_id(source_case):
    case = source_case
    assert case.real.admin_url.database == "rengu_fix_static_20261027"
    conn = await asyncpg.connect(
        case.real.admin_url.set(drivername="postgresql").render_as_string(hide_password=False)
    )
    try:
        await conn.execute(f'''CREATE TABLE "{case.real.schema}".pk_sample (
            ontology_id bigint NOT NULL, row_id bigint NOT NULL, source_code text)''')
        await conn.execute(f'''INSERT INTO "{case.real.schema}".pk_sample
            SELECT 1, n, 'K' || lpad((CASE WHEN n=1285 THEN 1 WHEN n=1286 THEN 2 ELSE n END)::text,4,'0')
            FROM generate_series(1,1286) n''')
        await conn.execute(f'''INSERT INTO "{case.real.schema}".pk_sample VALUES (2,9999,'K0001')''')
        await conn.execute(f'''ALTER TABLE "{case.real.schema}".pk_sample ENABLE ROW LEVEL SECURITY''')
        await conn.execute(f'''ALTER TABLE "{case.real.schema}".pk_sample FORCE ROW LEVEL SECURITY''')
        await conn.execute(f'''CREATE POLICY scope ON "{case.real.schema}".pk_sample
            USING (ontology_id=NULLIF(current_setting('rengu.ontology_id',true),'')::bigint)''')
        await conn.execute(f'''GRANT SELECT ON "{case.real.schema}".pk_sample TO "{case.real.reader}"''')
    finally:
        await conn.close()
    case.manifest[0].update(table="pk_sample", columns=["row_id", "source_code"])
    case.file.write_text(json.dumps(case.manifest), encoding="utf-8")
    properties = {
        "id": {"type": "int", "source": "row_id"},
        "code": {"type": "string", "source": "source_code"},
    }
    values = {"source": case.obj.datasource_name, "table": f"{case.real.schema}.pk_sample", "pk": "code"}
    actor = await resolve_user(case.session, "U010001")
    before = case.obj.pk_property
    status, note = await sample_primary_key(case.session, case.obj.code, case.obj, values, properties, actor)
    assert status == "阻断" and "2组重复主键" in note and "1286条" in note
    assert "K0001" not in note and "K0002" not in note
    values["pk"] = "id"
    status, note = await sample_primary_key(case.session, case.obj.code, case.obj, values, properties, actor)
    assert status == "通过" and "1286条" in note and "不代表全源唯一" in note
    assert case.obj.pk_property == before, "Sampling the draft id cannot publish or mutate the live key"
    properties["id"]["sensitive"] = True
    assert (await sample_primary_key(case.session, case.obj.code, case.obj, values, properties, actor))[
        0
    ] == "不适用"
